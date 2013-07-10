"""
Classes for generating diff coverage reports.
"""

from abc import ABCMeta, abstractmethod
from jinja2 import Environment, PackageLoader
from lazy import lazy
from collections import namedtuple
from pygments import highlight
from pygments.lexers import PythonLexer
from pygments.formatters import HtmlFormatter

class DiffViolations(object):
    """
    Class to capture violations generated by a particular diff
    """
    def __init__(self, violations, measured_lines, diff_lines):
        self.violations = violations
        self.lines = set(violation.line for violation in violations).intersection(diff_lines)

        if measured_lines is None:
            self.measured_lines = set(diff_lines)
        else:
            self.measured_lines = set(measured_lines).intersection(diff_lines)


class BaseReportGenerator(object):
    """
    Generate a diff coverage report.
    """

    __metaclass__ = ABCMeta

    def __init__(self, violations_reporter, diff_reporter):
        """
        Configure the report generator to build a report
        from `violations_reporter` (of type BaseViolationReporter)
        and `diff_reporter` (of type BaseDiffReporter)
        """
        self._violations = violations_reporter
        self._diff = diff_reporter

        self._cache_violations = None

    @abstractmethod
    def generate_report(self, output_file):
        """
        Write the report to `output_file`, which is a file-like
        object implementing the `write()` method.

        Concrete subclasses should access diff coverage info
        using the base class methods.
        """
        pass

    def coverage_report_name(self):
        """
        Return the name of the coverage report.
        """
        return self._violations.name()

    def diff_report_name(self):
        """
        Return the name of the diff.
        """
        return self._diff.name()

    def src_paths(self):
        """
        Return a list of source files in the diff
        for which we have coverage information.
        """
        return set(src for src, summary in self._diff_violations.items() if len(summary.measured_lines) > 0)

    def percent_covered(self, src_path):
        """
        Return a float percent of lines covered for the source
        in `src_path`.

        If we have no coverage information for `src_path`, returns None
        """
        diff_violations = self._diff_violations.get(src_path)

        if diff_violations is None:
            return None

        uncovered = diff_violations.lines
        percent_covered = 100 - float(len(uncovered)) / len(diff_violations.measured_lines) * 100

        return float(percent_covered)

    def missing_lines(self, src_path):
        """
        Return a list of missing lines (integers) in `src_path` that were changed.

        If we have no coverage information for `src_path`, returns
        an empty list.
        """

        diff_violations = self._diff_violations.get(src_path)

        if diff_violations is None:
            return []

        return sorted(diff_violations.lines)

    def total_num_lines(self):
        """
        Return the total number of lines in the diff for
        which we have coverage info.
        """

        return sum([len(summary.measured_lines) for summary
                    in self._diff_violations.values()])

    def total_num_missing(self):
        """
        Returns the total number of lines in the diff
        that should be covered, but aren't.
        """

        return sum(
            len(summary.lines)
            for summary
            in self._diff_violations.values()
        )

    def total_percent_covered(self):
        """
        Returns the float percent of lines in the diff that are covered.
        (only counting lines for which we have coverage info).
        """
        total_lines = self.total_num_lines()

        if total_lines > 0:
            num_covered = total_lines - self.total_num_missing()
            return int(float(num_covered) / total_lines * 100)

        else:
            return 100

    @lazy
    def _diff_violations(self):
        """
        Returns a dictionary of the form:

            { SRC_PATH: DiffViolations(SRC_PATH) }

        where `SRC_PATH` is the path to the source file.

        To make this efficient, we cache and reuse the result.
        """
        return {
            src_path: DiffViolations(
                self._violations.violations(src_path),
                self._violations.measured_lines(src_path),
                self._diff.lines_changed(src_path),
            ) for src_path in self._diff.src_paths_changed()
        }


PYG_LEXER = PythonLexer()
PYG_HTML_FORMATTER = HtmlFormatter(nowrap=True)
def handle_py_highlight(source):
    pretty_source = highlight(source, PYG_LEXER, PYG_HTML_FORMATTER)
    return pretty_source[:-1]  # Get rid of the newline at the end


# Set up the template environment
TEMPLATE_LOADER = PackageLoader(__package__)
TEMPLATE_ENV = Environment(loader=TEMPLATE_LOADER,
                           lstrip_blocks=True,
                           trim_blocks=True)
TEMPLATE_ENV.filters['py_highlight'] = handle_py_highlight

class TemplateReportGenerator(BaseReportGenerator):
    """
    Reporter that uses a template to generate the report.
    """

    # Subclasses override this to specify the name of the template
    # If not overridden, the template reporter will raise an exception
    TEMPLATE_NAME = None

    def generate_report(self, output_file):
        """
        See base class.
        """

        if self.TEMPLATE_NAME is not None:

            # Find the template
            template = TEMPLATE_ENV.get_template(self.TEMPLATE_NAME)

            # Render the template
            report = template.render(self._context())

            # Write the report to the output file
            # (encode to a byte string)
            output_file.write(report.encode('utf-8'))

    def _context(self):
        """
        Return the context to pass to the template.

        The context is a dict of the form:

        {'report_name': REPORT_NAME,
         'diff_name': DIFF_NAME,
         'src_stats': {SRC_PATH: {
                            'percent_covered': PERCENT_COVERED,
                            'num_missing': NUM_MISSING,
                            'hunks': [HUNK, ...]
                            }, ... }
         'total_num_lines': TOTAL_NUM_LINES,
         'total_num_missing': TOTAL_NUM_MISSING,
         'total_percent_covered': TOTAL_PERCENT_COVERED}

        where HUNKs are instances of HunkObjects
        """

        # Calculate the information to pass to the template
        src_stats = {src: self._src_path_stats(src)
                     for src in self.src_paths()}

        augmented_stats = {}
        for src in self.src_paths():
            hunker = Hunker(src, self.missing_lines(src), self._diff)
            augmented_stats[src] = hunker.classify()


        return {'report_name': self.coverage_report_name(),
                'diff_name': self.diff_report_name(),
                'src_stats': src_stats,
                'augmented_stats': augmented_stats,
                'total_num_lines': self.total_num_lines(),
                'total_num_missing': self.total_num_missing(),
                'total_percent_covered': self.total_percent_covered()}

    def _src_path_stats(self, src_path):
        """
        Return a dict of statistics for the source file at `src_path`.
        """
        # Find missing lines
        missing_lines = [str(line) for line
                         in self.missing_lines(src_path)]

        return {'percent_covered': self.percent_covered(src_path),
                'missing_lines': missing_lines,
                'num_missing': len(missing_lines)}

class StringReportGenerator(TemplateReportGenerator):
    """
    Generate a string diff coverage report.
    """
    TEMPLATE_NAME = "console_report.txt"


class HtmlReportGenerator(TemplateReportGenerator):
    """
    Generate an HTML formatted diff coverage report.
    """
    TEMPLATE_NAME = "html_report.html"


class Hunker(object):

    def __init__(self, src_path, missing_lines, diff_reporter):

        self.src_path = src_path
        self.line_numbers = missing_lines
        self.diff_reporter = diff_reporter
        self.lines_of_context = 2

    def hunkify(self):
        """

        """

        hunks = []
        if not self.line_numbers:
            return []
        current_hunk = [self.line_numbers[0]]

        for i in range(len(self.line_numbers)-1):
            if (self.line_numbers[i+1] - self.line_numbers[i]) < 2 * self.lines_of_context + 2:
                current_hunk.append(self.line_numbers[i+1])
            else:
                hunks.append(list(current_hunk))
                del current_hunk[:]
                current_hunk.append(self.line_numbers[i+1])

        return hunks


    def code_and_context(self):
        """
    
        """

        f = open(self.src_path)
        content = f.readlines()

        padded_hunks = []


        for hunk in self.hunkify():
            if hunk[0] - self.lines_of_context < 0:
                padded_hunks.append(zip(range(0, hunk[-1] + self.lines_of_context + 1), content[ : hunk[-1] + self.lines_of_context]))
            else:
                padded_hunks.append(zip(range(hunk[0] - self.lines_of_context, hunk[-1] + self.lines_of_context + 1), content[hunk[0] - self.lines_of_context - 1 : hunk[-1] + self.lines_of_context]))
            
       
        return padded_hunks

    def classify(self):

        toTemplate = []

        Line = namedtuple('line', 'line_num line_code line_type')

        for hunk in self.code_and_context():
            current_hunk = []

            for i in range(len(hunk)):
                if hunk[i][0] in self.line_numbers:
                    current_hunk.append(Line(hunk[i][0], hunk[i][1], 'VIOLATION'))
                elif hunk[i][0] in self.diff_reporter.lines_changed(self.src_path):
                    current_hunk.append(Line(hunk[i][0], hunk[i][1], 'NEWCONTEXT'))
                else:
                    current_hunk.append(Line(hunk[i][0], hunk[i][1], 'OLDCONTEXT'))

            toTemplate.append(current_hunk)
        return toTemplate
