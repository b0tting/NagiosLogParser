
from datetime import datetime
import os

## Const values
import re
from datetime import timedelta
from time import strftime

NAGIOS_OK = 0
NAGIOS_WARNING = 1
NAGIOS_CRITICAL = 2
NAGIOS_UNKNOWN = 3
NAGIOS_DICT = {NAGIOS_OK: "OK", NAGIOS_WARNING: "WARNING", NAGIOS_CRITICAL: "CRITICAL", NAGIOS_UNKNOWN: "UNKNOWN"}
RESULTBLOCK="[RESULT]"
SERVERBLOCK="[SERVER]"
CONFIG_FILE="logparseconfig.yaml"


def yamltime_to_timedelta(yamltime):
    if yamltime.endswith("d"):
        return timedelta(days=yamltime[:-1])
    elif yamltime.endswith("h"):
        return timedelta(hours=yamltime[:-1])
    elif yamltime.endswith("m"):
        return timedelta(minutes=yamltime[:-1])
    else:
        raise ValueError("Could not parse " + yamltime + " into a valid hours (h), minutes (m) or days (d) time")

class NagiosBoundaryCheck:
    def __init__(self, configDict, defaultMessage):
        ###
        # I expect warning or critical configuration to look like:
        # warning:
        #       expression: regex
        # <or>  lessthan: number
        # <or>  greaterthan: number
        ###
        if configDict == False:
            self.type = "fake"
        elif("expression" in configDict):
            self.type = "exp"
            self.boundary = configDict["expression"]
        elif("lessthan" in configDict):
            self.type = "lt"
            self.boundaryfloat = float(configDict["lessthan"])
        elif("greaterthan" in configDict):
            self.type = "gt"
            self.boundaryfloat = float(configDict["greaterthan"])
        else:
            raise ValueError("A warning or critical boundary should have an 'expression', 'lessthan' or 'greaterthan' value")
        self.message = defaultMessage if (not configDict or "message" not in configDict) else configDict["message"]

    def inBadState(self, value):
        if(self.type == "fake"):
            return False
        elif(self.type == "exp"):
            return re.match(self.boundary, value)
        else:
            try:
                if (self.type == "lt"):
                    return float(value) < self.boundaryfloat
                elif (self.type == "gt"):
                    return float(value) > self.boundaryfloat
            except ValueError as e:
                raise ValueError("check expected a numerical value from WebLogic but got '" + str(value) + "'")

    def getPerformanceIndicator(self):
        return self.boundaryfloat if hasattr(self, "boundaryfloat") else False

    def getMessage(self):
        return self.message

## I took this script from here:
## https://stackoverflow.com/a/23646049/
## The aim is to read large files bottom first, so I can parse dates and stop if we are no longer interested
## in older log lines
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


def parse(filename, filter, dateage, datecolumns, dateformat):
    filterexpression = re.compile(filter) if filter else False

    staletime = datetime.now() - yamltime_to_timedelta(dateage)

    for logline in reverse_readline(filename):
        # First, discard this line if it does not match the filter (or if the filter is empty)
        if not filterexpression or filterexpression.search(logline):
            # Second, parse the date to see if we are still actual. Break when done!
            if dateage:
                splitlist = logline.split()
                if datecolumns.length > 1:
                    loglinedate = splitlist[datecolumns[0]]
                    loglinedate += " " + splitlist[datecolumns[1]]
                else:
                    loglinedate = splitlist[datecolumns]
                parsetime = datetime.strptime(dateformat,loglinedate)
                if parsetime < staletime:
                    break


def getCheckNames(configurations):
    return [name for name in configurations["configurations"] if
            "url" in configurations["configurations"][name]]

if __name__ == "__main__":
    description = '''  
https://github.com/b0tting/NagiosLogParser (motting@qualogy.com)
This is a script that should memory-efficiently parse log files and return a 
nagios valid error message and code depending on a number of metrics. 
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

    if (os.path.exists(configfile)):
        try:
            configurations = yaml.load(open(configfile, "r"))
        except yaml.YAMLError, exc:
            if hasattr(exc, 'problem_mark'):
                mark = exc.problem_mark
                print "Error position: (%s:%s)" % (mark.line + 1, mark.column + 1)
            print("Could not parse YAML, " + str(exc))
    else:
        print("UNKNOWN: Could not find a YAML config file named " + configfile + "!")
        exit(4)

    if args.generatenrpe:
        result = "# NRPE entries generated by " + sys.argv[0] + "\n"
        pathname = os.path.realpath(__file__)
        for name in getCheckNames(configurations):
            result += "command[" + name + "]=/usr/bin/python " + pathname + " -c " + name + "\n"
        print result
        exit(0)

    if args.list:
        print(parser.description)
        print("Known checks:")
        known = "\n".join(getCheckNames(configurations))
        known += "\n"
        print(known)
        exit(0)

    if args.check:
        if args.check not in getCheckNames(configurations):
            print(
                        "UNKNOWN: Could not find " + args.check + " in the list of known checks. Run script with -h parameter to get a list of known checks.")
            exit(NAGIOS_UNKNOWN)
    else:
        print(parser.description)
        print(
            "No known checks or check name was given, so we will run all known checks for testing purposes. Run with -h for more options.\n")

    for name in getCheckNames(configurations):
        config = configurations["configurations"][name]

        ## Skip unnamed configurations as they are probably used as templates
        if not args.check or args.check == name:
            ## Some setup
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

            if nagiosMessage != "":
                nagiosMessage += ". "



            PARRSSSEEE

            ## If the error message is not empty
            if result[1]:
                nagiosResult = NAGIOS_UNKNOWN
                nagiosMessage += server + " reports " + result[1]
            else:
                try:
                    if criticalCheck.inBadState(result[0]):
                        nagiosResult = NAGIOS_CRITICAL if nagiosResult < NAGIOS_CRITICAL else nagiosResult
                        nagiosMessage += criticalCheck.getMessage()
                    elif warningCheck.inBadState(result[0]):
                        nagiosResult = NAGIOS_WARNING if nagiosResult < NAGIOS_WARNING else nagiosResult
                        nagiosMessage += warningCheck.getMessage()
                    else:
                        nagiosMessage += config["message"]
                except ValueError as e:
                    nagiosResult = NAGIOS_UNKNOWN
                    nagiosMessage += "Unexpected result, " + str(e)

            ## After handling the result, transform macros in the message
            if nagiosMessage.find(RESULTBLOCK) > -1:
                nagiosMessage = nagiosMessage.replace(RESULTBLOCK, result[0])
            if nagiosMessage.find(SERVERBLOCK) > -1:
                nagiosMessage = nagiosMessage.replace(SERVERBLOCK, server)

            ## Now add performance data
            if performanceData:
                if nagiosPerformanceData != "":
                    nagiosPerformanceData += " "

                nagiosPerformanceData += "'" + server + "'=" + result[0]
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

            ## If running just one check, exit here
            if args.check:
                exit(nagiosResult)
    exit(0)