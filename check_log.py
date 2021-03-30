import argparse
import sys
from datetime import datetime
import os
import re
from datetime import timedelta
# The pyyaml lib
import yaml

NAGIOS_OK = 0
NAGIOS_WARNING = 1
NAGIOS_CRITICAL = 2
NAGIOS_UNKNOWN = 3
NAGIOS_DICT = {NAGIOS_OK: "OK", NAGIOS_WARNING: "WARNING", NAGIOS_CRITICAL: "CRITICAL", NAGIOS_UNKNOWN: "UNKNOWN"}
RESULTBLOCK="[RESULT]"
LOGFILEBLOCK="[LOGFILE]"
DATEAGEBLOCK="[DATEAGE]"
CONFIG_FILE="logparseconfig.yaml"
VERBOSE = False


def printIfVerbose(string):
    if VERBOSE:
        print(str(string))


def yamltime_to_timedelta(yamltime):
    scalar = int(yamltime[:-1])
    try:
        if yamltime.endswith("d"):
            return timedelta(days=scalar)
        elif yamltime.endswith("h"):
            return timedelta(hours=scalar)
        elif yamltime.endswith("m"):
            return timedelta(minutes=scalar)
    except Exception as e:
        raise ValueError("Could not parse " + yamltime + " into a valid hours (h), minutes (m) or days (d) time")


class NagiosBoundaryCheck:
    def __init__(self, config_dict, default_message):
        #
        # I expect warning or critical configuration to look like:
        # warning:
        #       expression: regex
        # <or>  lessthan: number
        # <or>  greaterthan: number
        #
        if not config_dict:
            self.type = "fake"
        elif "expression" in config_dict:
            self.type = "exp"
            self.boundary = config_dict["expression"]
        elif "lessthan" in config_dict:
            self.type = "lt"
            self.boundaryfloat = float(config_dict["lessthan"])
        elif "greaterthan" in config_dict:
            self.type = "gt"
            self.boundaryfloat = float(config_dict["greaterthan"])
        else:
            raise ValueError("A warning or critical boundary should have an 'expression', 'lessthan' or 'greaterthan' value")
        self.message = default_message if (not config_dict or "message" not in config_dict) else config_dict["message"]

    def inBadState(self, value):
        if self.type == "fake":
            return False
        elif self.type == "exp":
            return re.match(self.boundary, value)
        else:
            try:
                if self.type == "lt":
                    return float(value) < self.boundaryfloat
                elif self.type == "gt":
                    return float(value) > self.boundaryfloat
            except ValueError as e:
                raise ValueError("check expected a numerical value from WebLogic but got '" + str(value) + "'")

    def getPerformanceIndicator(self):
        return self.boundaryfloat if hasattr(self, "boundaryfloat") else False

    def getMessage(self):
        return self.message


# I took this script from this stack post by user "srohde":
#      https://stackoverflow.com/a/23646049/
# The aim is to read large files bottom first, so I can parse dates and stop if we are no longer interested
# in older log lines
def reverse_readline(filename, buf_size=8192):
    """A generator that returns the lines of a file in reverse order"""
    with open(filename) as fh:
        segment = None
        offset = 0
        fh.seek(0, os.SEEK_END)
        file_size = remaining_size = fh.tell()
        while remaining_size > 0:
            offset = min(file_size, offset + buf_size)
            fh.seek(file_size - offset)
            buffer = fh.read(min(remaining_size, buf_size))
            remaining_size -= buf_size
            lines = buffer.split('\n')
            # The first line of the buffer is probably not a complete line so
            # we'll save it and append it to the last line of the next buffer
            # we read
            if segment is not None:
                # If the previous chunk starts right from the beginning of line
                # do not concat the segment to the last line of new chunk.
                # Instead, yield the segment first
                if buffer[-1] != '\n':
                    lines[-1] += segment
                else:
                    yield segment
            segment = lines[0]
            for index in range(len(lines) - 1, 0, -1):
                if lines[index]:
                    yield lines[index]
        # Don't yield None if the file was empty
        if segment is not None:
            yield segment


class LogFile:
    def __init__(self, filename):
        self.basename = os.path.basename(filename)
        self.file = filename
        if not os.path.exists(filename):
            raise IOError("Could not find logfile " + self.basename)
        elif not os.access(self.file, os.R_OK):
            raise IOError("Could find but not read logfile " + self.basename)
        self.size = os.path.getsize(self.file)

    def is_stale(self, staledate):
        mtime = os.stat(self.file).st_mtime
        lastmod = datetime.fromtimestamp(mtime)
        allowedage = yamltime_to_timedelta(staledate)
        return datetime.now() - allowedage > lastmod

    def is_null(self):
        return self.size == 0

    def is_more_mb_then(self, largesize):
        return self.size > largesize * 1024 * 1024


class ColumnDateExtractor:
    def __init__(self, datecolumns):
        self.columns = [int(col) for col in str(datecolumns).split(",")]

    def extract_date_string(self, logline):
        splitlist = logline.split()
        if len(self.columns) > 1:
            loglinedate = splitlist[self.columns[0]]
            loglinedate += " " + splitlist[self.columns[1]]
        else:
            loglinedate = splitlist[self.columns[0]]
        return loglinedate


class ExpressionDateExtractor:
    def __init__(self, dateexpression ):
        self.expression = re.compile(str(dateexpression))

    def extract_date_string(self, logline):
        match = self.expression.search(logline)
        return match.group(1) if match else "(no date match)"


def check(config):
    printIfVerbose(config)
    # Before anything, check if our logfile exists
    error = None
    try:
        logfile = LogFile(config["logfile"])
        if "stalealert" in config and logfile.is_stale(config["stalealert"]):
            error = "The log file " + os.path.basename(config["logfile"]) + " was older than " + config[
                "stalealert"] + " and is considered stale."
        if "nullalert" in config and logfile.is_null():
            error = "The log file " + os.path.basename(config["logfile"]) + " is 0 bytes"
        if "sizealert" in config and logfile.is_more_mb_then(config["sizealert"]):
            error = "The log file " + os.path.basename(config["logfile"]) + " is larger than the allowed " + config[
                "sizealert"] + "mb and will not be parsed"
    except IOError as e:
        error = str(e)

    if error:
        return 0, error
    else:
        linecount = 0
        count = 0
        avg = 0.0
        avgcolumn = int(config["avgcolumn"]) if "avgcolumn" in config else False
        filterexpression = re.compile(str(config["filter"])) if "filter" in config else False

        donetime = datetime.now() - yamltime_to_timedelta(config["dateage"]) if "dateage" in config else False
        if donetime:
            if config.get("dateexpression"):
                extractor = ExpressionDateExtractor(config.get("dateexpression"))
            elif config.get("datecolumn"):
                extractor = ColumnDateExtractor(config.get("datecolumn"))
            else:
                raise ValueError("Dateage was given, but no dateexpression or datecolumn for where to find the date")
        else:
            extractor = False

        datelinehack = config["datesearchall"] if "datesearchall" in config else False
        dateignoreerrors = "dateignoreerrors" in config and config["dateignoreerrors"]

        # Start reading the logfile bottom first
        for logline in reverse_readline(config["logfile"]):
            matching = filterexpression.search(logline)
            # Only continue if:
            # - this line matches the filter OR
            # - we need to check if there is a valid date in this line
            if not filterexpression or matching or datelinehack:
                # Second, parse the date to see if we are still actual. Break when done!
                if extractor:
                    loglinedate = extractor.extract_date_string(logline)
                    try:
                        parsetime = datetime.strptime(loglinedate, config["dateformat"])
                        if config.get("dateignoreyear"):
                            parsetime = parsetime.replace(year=donetime.year)

                        if parsetime < donetime:
                            # The most important break. This means we parsed a date older then the max age of the log
                            break

                    except ValueError:
                        printIfVerbose("Could not parse " + loglinedate + " from line " + str(linecount) + " from bottom")
                        if not datelinehack and not dateignoreerrors:
                            error = "Could not extract a valid date from '" + loglinedate + "'"
                            break

                    if avgcolumn:
                        avg += float(logline.split(" ")[avgcolumn])

                if matching:
                    count += 1
            linecount += 1

        if avgcolumn:
            if count == 0:
                return avg, "No recent or unfiltered log lines, so no valid average could be calculated"
            else:
                return avg, error
        else:
            return count, error

# Here we figure out if this is a template or an actual check, using the message attribute to discriminate
def getCheckNames(configurations):
    return [name for name in configurations["configurations"] if
            "message" in configurations["configurations"][name]]

if __name__ == "__main__":
    description = '''  
https://github.com/b0tting/NagiosLogParser (motting@qualogy.com)
This is a script that should memory-efficiently parse log files and return a 
nagios valid error message and code depending on the number of times a certain
metric appeared.   
Note that this script requires a valid config file. 
'''
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("-c", "--check",
                        help="Check name to run. If none, all are run (useless in a Nagios context)")
    parser.add_argument("-l", "--list", action='store_true', help="List checks in the current config")
    parser.add_argument("-y", "--yamlconfig",
                        help="Location of a YAML config, assumes logparseconfig.yaml if none is given")
    parser.add_argument("-v", "--verbose", action='store_true',
                        help="Enable verbose logging, not useable in Nagios contexts")
    parser.add_argument("-g", "--generatenrpe", action="store_true",
                        help="Generate the NRPE lines for all known checks")
    args = parser.parse_args()

    if args.yamlconfig:
        configfile = args.yamlconfig
    else:
        configfile = CONFIG_FILE
        if not os.path.exists(configfile):
            configfile = os.path.join(os.path.dirname(os.path.realpath(__file__)), configfile)

    if os.path.exists(configfile):
        try:
            if hasattr(yaml, "FullLoader"):
                configurations = yaml.load(open(configfile, "r"), Loader=yaml.FullLoader)
            else:
                configurations = yaml.load(open(configfile, "r"))
        except yaml.YAMLError:
            _, exc, _ = sys.exc_info()
            if hasattr(exc, 'problem_mark'):
                mark = exc.problem_mark
                print("Error position: (%s:%s)" % (mark.line + 1, mark.column + 1))
            print("Could not parse YAML, " + str(exc))
            exit(4)
    else:
        print("UNKNOWN: Could not find a YAML config file named " + configfile + "!")
        exit(4)

    if args.generatenrpe:
        result = "# NRPE entries generated by " + sys.argv[0] + "\n"
        pathname = os.path.realpath(__file__)
        for name in getCheckNames(configurations):
            result += "command[" + name + "]=/usr/bin/python " + pathname + " -c " + name + "\n"
        print(result)
        exit(0)

    if args.list:
        print(parser.description)
        print("Known checks:")
        known = "\n".join(getCheckNames(configurations))
        known += "\n"
        print(known)
        exit(0)

    VERBOSE = args.verbose

    if args.check:
        if args.check not in getCheckNames(configurations):
            print("UNKNOWN: Could not find " + args.check + " in the list of known checks. Run script with -h parameter to get a list of known checks.")
            exit(NAGIOS_UNKNOWN)
    else:
        print(parser.description)
        print("No known checks or check name was given, so we will run all known checks for testing purposes. Run with -h for more options.\n")

    checks = getCheckNames(configurations)
    if len(checks) == 0:
        print("Although the file exists and is a YAML file, there were no valid checks in the given configuration file")
        exit(0)

    for name in checks:
        printIfVerbose("First check up is " + name)
        config = configurations["configurations"][name]

        # Skip unnamed configurations as they are probably used as templates
        if not args.check or args.check == name:
            # Some setup
            nagiosResult = NAGIOS_OK
            nagiosMessage = ""
            nagiosPerformanceData = ""

            warningCheck = NagiosBoundaryCheck(False if "warning" not in config else config["warning"],
                                               config["message"])
            criticalCheck = NagiosBoundaryCheck(False if "critical" not in config else config["critical"],
                                                config["message"])
            performanceData = False if "performancedata" not in config else config["performancedata"]
            unknownToCrit = False if "unknownascritical" not in config else config["unknownascritical"]
            params = False if "parameters" not in config else config["parameters"]

            result, error = check(config)

            # If the error message is not empty
            if error:
                nagiosResult = NAGIOS_UNKNOWN
                nagiosMessage = error
            else:
                try:
                    if criticalCheck.inBadState(result):
                        nagiosResult = NAGIOS_CRITICAL if nagiosResult < NAGIOS_CRITICAL else nagiosResult
                        nagiosMessage += criticalCheck.getMessage()
                    elif warningCheck.inBadState(result):
                        nagiosResult = NAGIOS_WARNING if nagiosResult < NAGIOS_WARNING else nagiosResult
                        nagiosMessage += warningCheck.getMessage()
                    else:
                        nagiosMessage += config["message"]
                except ValueError as e:
                    nagiosResult = NAGIOS_UNKNOWN
                    nagiosMessage += "Unexpected result, " + str(e)

            # After handling the result, transform macros in the message
            nagiosMessage = nagiosMessage.replace(RESULTBLOCK, str(result))
            nagiosMessage = nagiosMessage.replace(LOGFILEBLOCK, config["logfile"])
            if "dateage" in config:
                nagiosMessage = nagiosMessage.replace(DATEAGEBLOCK, config["dateage"])

            # Now add performance data
            if performanceData:
                nagiosPerformanceData += "'"+name+"'=" + str(result)
                nagiosPerformanceData += ";"
                if warningCheck.getPerformanceIndicator():
                    nagiosPerformanceData += str(warningCheck.getPerformanceIndicator())
                if criticalCheck.getPerformanceIndicator():
                    nagiosPerformanceData += ";" + str(criticalCheck.getPerformanceIndicator())

            if unknownToCrit and nagiosResult == NAGIOS_UNKNOWN:
                nagiosResult = NAGIOS_CRITICAL

            if performanceData:
                print(NAGIOS_DICT[nagiosResult] + ": " + nagiosMessage + " | " + nagiosPerformanceData)
            else:
                print(NAGIOS_DICT[nagiosResult] + ": " + nagiosMessage)

            # If running just one check, exit here
            if args.check:
                exit(nagiosResult)
    exit(0)