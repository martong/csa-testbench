from collections import defaultdict
from cgi import escape
import json

try:
    import plotly.offline as py
    import plotly.graph_objs as go
    charts_supported = True
except ImportError:
    charts_supported = False

header = """
<!DOCTYPE html>
<html lang="en">
<head>
  <title>Detailed Statistics</title>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.1.0/css/bootstrap.min.css"
        integrity="sha384-9gVQ4dYFwwWSjIDZnLEWnxCjeSWFphJiwGPXr1jddIhOegiu1FwO5qRGvFXOdJZ4"
        crossorigin="anonymous">
  <script src="https://code.jquery.com/jquery-3.3.1.slim.min.js"
          integrity="sha384-q8i/X+965DzO0rT7abK41JStQIAqVgRVzpbzo5smXKp4YfRvH+8abtTE1Pi6jizo"
          crossorigin="anonymous"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/popper.js/1.14.0/umd/popper.min.js"
          integrity="sha384-cs/chFZiN24E4KMATLdqdvsezGxaGsi4hLGOzlXwp5UZB1LY//20VyM2taTB4QvJ"
          crossorigin="anonymous"></script>
  <script src="https://stackpath.bootstrapcdn.com/bootstrap/4.1.0/js/bootstrap.min.js"
          integrity="sha384-uefMccjFJAIv6A+rW+L4AHf99KvxDjWSu1z9VI8SKNVmz4sk7buKt/6v9KI65qnm"
          crossorigin="anonymous"></script>
  <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
</head>
<body>
<div class="jumbotron text-center">
  <h1>Detailed Static Analyzer Statistics</h1>
</div>
<div class="container">
<h1>Tables</h1>
"""

footer = """
</div>
<footer>
  <div class="container-fluid bg-light p-3 mb-2">
    <span class="text-muted">This report is created by the <a href="https://github.com/Xazax-hun/csa-testbench">CSA-Testbench</a> toolset.</span>
  </div>
</footer>
</body>
</html>
"""


# FIXME: Escape strings.
class HTMLPrinter(object):

    def __init__(self, path, config):
        self.html_path = path
        self.charts = config.get("charts", ["Duration", "Result count"])
        self.excludes = ["TU times"]
        self.as_comment = ["Analyzer version"]
        self.projects = {}
        with open(self.html_path, 'w') as stat_html:
            stat_html.write(header)
            stat_html.write("<!-- %s -->\n" %
                            escape(json.dumps(config)))

    def finish(self):
        with open(self.html_path, 'a') as stat_html:
            self._generate_charts(stat_html)
            stat_html.write(footer)

    def extend_with_project(self, name, data):
        self.projects[name] = data
        stat_html = open(self.html_path, 'a')
        keys = set()
        configurations = set()
        for configuration, val in data.iteritems():
            configurations.add(configuration)
            for stat_name in val:
                keys.add(stat_name)

        stat_html.write("<h2>%s</h2>\n" % escape(name))
        stat_html.write('<table class="table table-bordered table-striped table-sm">\n')
        stat_html.write('<thead class="thead-dark">')
        stat_html.write("<tr>\n")
        stat_html.write("<th>Statistic Name</th>")
        for conf in configurations:
            stat_html.write("<th>%s</th>" % escape(conf))
        stat_html.write("</tr>\n")
        stat_html.write('</thread>\n')
        stat_html.write('<tbody>\n')

        for stat_name in keys:
            if stat_name in self.excludes or \
               stat_name in self.as_comment:
                continue
            stat_html.write("<tr>\n")
            stat_html.write("<td>%s</td>" % escape(stat_name))
            for conf in configurations:
                val = "-"
                if stat_name in data[conf]:
                    val = str(data[conf][stat_name])
                stat_html.write("<td>%s</td>" % val)
            stat_html.write("</tr>\n")
        stat_html.write('</tbody>\n')
        stat_html.write("</table>\n\n")

        # Output some values as comments.
        for stat_name in self.as_comment:
            for conf in configurations:
                val = "-"
                if stat_name in data[conf]:
                    val = str(data[conf][stat_name])
                stat_html.write("<!-- %s[%s]=%s -->\n" %
                                (escape(conf), escape(stat_name), escape(val)))

        self._generate_time_histogram(stat_html, configurations, data)
        stat_html.close()

    def _generate_time_histogram(self, stat_html, configurations, data):
        if not charts_supported:
            return
        traces = []
        for conf in configurations:
            if "TU times" in data[conf]:
                if len(data[conf]["TU times"]) == 0:
                    continue
                traces.append(go.Histogram(x=data[conf]["TU times"],
                                           name=conf))
        if len(traces) == 0:
            return
        layout = go.Layout(barmode='overlay')
        fig = go.Figure(data=traces, layout=layout)
        div = py.plot(fig, show_link=False, include_plotlyjs=False,
                      output_type='div', auto_open=False)
        stat_html.write("<h3>Time per TU histogram</h3>\n")
        stat_html.write(div)

    def _generate_charts(self, stat_html):
        if not charts_supported:
            return
        stat_html.write("<h1>Charts</h1>\n")
        layout = go.Layout(barmode='group')
        for chart in self.charts:
            names = defaultdict(list)
            values = defaultdict(list)
            for project, data in self.projects.iteritems():
                for configuration, stats in data.iteritems():
                    if chart in stats:
                        values[configuration].append(float(stats[chart]))
                    else:
                        values[configuration].append(0)
                    names[configuration].append(project)

            # Skip empty charts.
            if all([all([x == 0 for x in values[conf]]) for conf in names]):
                continue

            bars = []
            for conf in names:
                bar = go.Bar(x=names[conf], y=values[conf], name=conf)
                bars.append(bar)

            fig = go.Figure(data=bars, layout=layout)
            div = py.plot(fig, show_link=False, include_plotlyjs=False,
                          output_type='div', auto_open=False)
            stat_html.write("<h2>%s</h2>\n" % escape(chart))
            stat_html.write(div)
