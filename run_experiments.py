from __future__ import print_function
import argparse as ap
import json
import multiprocessing
import os
import shlex
import shutil
import subprocess as sp
import sys
import tempfile

from summarize_sa_stats import summ_stats
from generate_stat_html import print_stats_html

TESTBENCH_ROOT = os.path.dirname(os.path.abspath(__file__))


def load_config(filename):
    """Load all information from the specified config file."""

    config_path = os.path.join(TESTBENCH_ROOT, filename)
    with open(config_path, 'r') as config_file:
        config_dict = json.loads(config_file.read())
    if not config_dict:
        sys.stderr.write("[Error] Empty config file.\n")
        sys.exit(1)
    return config_dict


def run_command(cmd, print_error=True, cwd=None, env=None):
    """Wrapper function to handle running system commands."""

    proc = sp.Popen(shlex.split(cmd), stdin=sp.PIPE, stdout=sp.PIPE,
                    stderr=sp.PIPE, cwd=cwd, env=env)
    stdout, stderr = proc.communicate()
    # CC usually does not return with 0, but printing empty
    # error messages in that case is needless.
    if proc.returncode is not 0 and print_error:
        sys.stderr.write("[ERROR] %s\n" % str(stderr))
    return proc.returncode, stdout, stderr


def count_lines(project, project_dir):
    cloc_failed, stdout, _ = run_command("cloc %s --json" % project_dir)
    if not cloc_failed:
        try:
            cloc_json_out = json.loads(stdout)
            project["LOC"] = cloc_json_out["SUM"]["code"]
        except:
            pass
    print("[%s] LOC: %s." % (project['name'],
                             project['LOC'] if 'LOC' in project else '?'))


def clone_project(project, project_dir):
    """Clone a single project.

    Its version is specified by a version tag or a commit hash
    found in the config file.

    If a project already exists, we simply overwrite it.
    """
    if 'prepared' in project:
        count_lines(project, project_dir)
        return True

    # If the project folder already exists, remove it.
    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir)

    # If there is no tag specified, we clone the master branch.
    # This presumes that a master branch exists.
    if 'tag' not in project:
        project['tag'] = 'master'

    # Check whether the project config contains a version tag or a commit hash.
    try:
        int(project['tag'], base=16)
        commit_hash = True
    except:
        commit_hash = False

    # If the 'tag' value is a version tag, we can use shallow cloning.
    # With a commit hash, we need to clone everything and then checkout
    # the specified commit.
    cmd = {
        'clone': 'git clone %s %s' % (project['url'], project_dir)}

    if commit_hash:
        cmd['checkout'] = 'git -C %s checkout %s' % (
            project_dir, project['tag'])
    else:
        cmd['clone'] += ' --depth 1 --branch %s --single-branch' % project['tag']

    print("[%s] Checking out project... " % project['name'])
    sys.stdout.flush()
    clone_failed, _, clone_err = run_command(cmd['clone'], print_error=False)
    if 'master' in str(clone_err):
        clone_failed, _, _ = run_command(
            'git clone %s %s' % (project['url'], project_dir))
    if clone_failed:
        return False
    if 'checkout' in cmd:
        checkout_failed, _, _ = run_command(cmd['checkout'])
        if checkout_failed:
            return False

    count_lines(project, project_dir)

    return True


def identify_build_system(project_dir):
    """Identifies the build system of a project.

    Used heuristics:
        - If there's a 'CMakeLists.txt' file at the project root: 'cmake'.
        - If there's an 'autogen.sh' script at the project root: run it.
        - If there's a 'configure' script at the project root: run it,
          then return 'makefile'.

    FIXME: If no build system found, should we apply the same
           heuristics for src subfolder if exists?

    The actual build-log generation happens in main().
    """

    project_files = os.listdir(project_dir)
    if not project_files:
        sys.stderr.write("[ERROR] No files found in '%s'.\n" % project_dir)
        return None

    if 'CMakeLists.txt' in project_files:
        return 'cmake'

    if 'Makefile' in project_files:
        return 'makefile'

    if 'autogen.sh' in project_files:
        # Autogen needs to be executed in the project's root directory.
        os.chdir(project_dir)
        autogen_failed, _, _ = run_command("sh autogen.sh")
        os.chdir(os.path.dirname(project_dir))
        if autogen_failed:
            return None

    # Need to re-list files, as autogen might have generated a config script.
    project_files = os.listdir(project_dir)

    if 'configure' in project_files:
        os.chdir(project_dir)
        configure_failed, _, _ = run_command("./configure")
        os.chdir(os.path.dirname(project_dir))
        if configure_failed:
            return None
        return 'makefile'

    sys.stderr.write("[ERROR] Build system cannot be identified.\n")
    return None


def check_logged(projects_root):
    """Post-script cleanup.

    Removes any projects that have an empty build-log JSON file
    at the end of the script.

    FIXME: instead of listing all directories only list the ones
           that were in the config file. Or move the check to
           log_project.
    """

    projects = os.listdir(projects_root)
    for project in projects:
        if os.path.isfile(project):
            continue
        log = os.path.join(projects_root, project, 'compile_commands.json')
        if os.path.getsize(log) == 0:
            shutil.rmtree(os.path.join(projects_root, project))
    return os.listdir(projects_root)


def log_project(project, project_dir, num_jobs):
    # The following runs the 'configure' script if needed.
    if 'prepared' in project:
        return True
    if 'make_command' in project:
        build_sys = 'userprovided'
    else:
        build_sys = identify_build_system(project_dir)
    failed = False
    if not build_sys:
        failed = True
    print("[%s] Generating build log... " % project['name'])
    if build_sys == 'cmake':
        cmd = "cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -B%s -H%s" \
              % (project_dir, project_dir)
        failed, _, _ = run_command(cmd)
    elif build_sys == 'makefile':
        json_path = os.path.join(project_dir, "compile_commands.json")
        cmd = "CodeChecker log -b 'make -C%s -j%d' -o %s" \
              % (project_dir, num_jobs, json_path)
        failed, _, _ = run_command(cmd)
    elif build_sys == 'userprovided':
        json_path = os.path.join(project_dir, "compile_commands.json")
        cmd = "CodeChecker log -b '%s' -o %s" \
              % (project['make_command'], json_path)
        failed, _, _ = run_command(cmd, True, project_dir)
    if failed:
        shutil.rmtree(project_dir)
        return False

    return True


def collect_args(arg_name, configuration_sources):
    return " ".join([conf[arg_name] if arg_name in conf else ""
                     for conf in configuration_sources])


def update_path(path, env=None):
    if env is None:
        env = os.environ
    env["PATH"] = path + ":" + env["PATH"]
    return env


def check_project(project, project_dir, config, num_jobs):
    """Analyze project and store the results with CodeChecker."""

    json_path = os.path.join(project_dir, "compile_commands.json")
    if "configurations" not in project:
        if "configurations" in config:
            project["configurations"] = config["configurations"]
        else:
            project["configurations"] = [{"name": "", "clang_sa_args": ""}]
    for run_config in project["configurations"]:
        result_dir = "cc_results"
        if run_config["name"] != "":
            result_dir += "_" + run_config["name"]
        result_path = os.path.join(project_dir, result_dir)
        coverage_dir = os.path.join(result_path, "coverage")
        run_config["result_path"] = result_path
        run_config["coverage_dir"] = coverage_dir
        args_file, filename = tempfile.mkstemp()
        os.write(args_file, " -Xclang -analyzer-config -Xclang record-coverage=%s "
                 % coverage_dir)
        conf_sources = [config["CodeChecker"], project, run_config]
        os.write(args_file, collect_args("clang_sa_args", conf_sources))
        os.close(args_file)
        tag = project["tag"] if "tag" in project else ""
        name = project["name"]
        if tag != "":
            name += "_" + tag
        if run_config["name"] != "":
            name += "_" + run_config["name"]
        run_config["full_name"] = name
        print("[%s] Analyzing project... " % name)
        sys.stdout.flush()
        env = None
        if "clang_path" in run_config:
            env = update_path(run_config["clang_path"])
        cmd = ("CodeChecker analyze '%s' -j%d -o %s -q " +
               "--analyzers clangsa --capture-analysis-output") \
              % (json_path, num_jobs, result_path)
        cmd += " --saargs " + filename
        cmd += collect_args("analyze_args", conf_sources)
        run_command(cmd, print_error=False, env=env)
        print("Done. Storing results...")

        cmd = "CodeChecker store %s --url '%s' -n %s --tag %s" \
              % (result_path, config["CodeChecker"]["url"], name, tag)
        cmd += collect_args("store_args", conf_sources)
        run_command(cmd, print_error=False, env=env)
        print("[%s] Results stored." % name)


def post_process_project(project, project_dir, config, num_jobs):
    for run_config in project["configurations"]:
        stats_dir = os.path.join(run_config["result_path"], "success")
        stats = json.dumps(summ_stats(stats_dir, False), indent=2)
        stats_result = os.path.join(run_config["result_path"], "stats.json")
        with open(stats_result, "w") as res_file:
            res_file.write(stats)
        print_stats_html(run_config["full_name"], stats_result,
                         os.path.join(os.path.dirname(project_dir), "stats.html"))

        if os.path.isdir(run_config["coverage_dir"]):
            cov_result_path = os.path.join(run_config["result_path"], "coverage_merged")
            try:
                run_command("MergeCoverage.py -i %s -o %s" %
                            (run_config["coverage_dir"], cov_result_path))
            except OSError as oerr:
                print("[Warning] MergeCoverage.py is not found in path.")
            cov_result_html = os.path.join(run_config["result_path"], "coverage.html")
            try:
                run_command("gcovr -g %s --html --html-details -r %s -o %s" %
                            (cov_result_path, project_dir, cov_result_html))
            except OSError as oerr:
                print("[Warning] gcovr is not found in path.")
        print("[%s] Post processed." % run_config['full_name'])


def main():
    parser = ap.ArgumentParser(description="Run differential analysis " +
                                           "experiment on a set of projects.",
                               formatter_class=ap.RawTextHelpFormatter)
    parser.add_argument("--config", metavar="FILE",
                        default='test_config.json',
                        help="JSON file holding a list of projects")
    parser.add_argument("-j", "--jobs", metavar="JOBS", type=int,
                        default=multiprocessing.cpu_count(),
                        help="number of jobs")
    args = parser.parse_args()

    try:
        run_command("CodeChecker version")
    except OSError as oerr:
        sys.stderr.write(
            "[ERROR] CodeChecker is not available as a command.\n")
        sys.exit(1)

    if args.jobs < 1:
        sys.stderr.write(
            "[ERROR] The number of jobs must be a positive integer.\n")

    config_path = os.path.join(TESTBENCH_ROOT, args.config)
    print("Using configuration file '%s'." % config_path)
    config = load_config(config_path)
    print("Number of projects to process: %d.\n" % len(config['projects']))

    projects_root = os.path.join(TESTBENCH_ROOT, 'projects')
    if not os.path.isdir(projects_root):
        os.mkdir(projects_root)

    for project in config['projects']:
        project_dir = os.path.join(projects_root, project['name'])
        if not clone_project(project, project_dir):
            shutil.rmtree(project_dir)
            continue
        if not log_project(project, project_dir, args.jobs):
            continue
        check_project(project, project_dir, config, args.jobs)
        post_process_project(project, project_dir, config, args.jobs)

    logged_projects = check_logged(projects_root)
    print("Number of analyzed projects: %d / %d"
          % (len(logged_projects), len(config['projects'])))
    print("Results can be viewed at '%s'." % config['CodeChecker']['url'])


if __name__ == '__main__':
    main()
