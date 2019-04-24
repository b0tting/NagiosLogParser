# Nagios logfile monitoring 

This is a Nagios compatible logfile check script, similar to another script I wrote [here](https://github.com/b0tting/OFMWRestMonitor). The main use is to smartly parse log files, roll back to a certain date and count the number of occurrences of a given filter value. 

Use cases would be to count the number of times a HTTP 500 error occured for a certain URL in an Apache logfile, for example. Or how often a "NumberValueException" appeared in a java STDOUT log file in the last hour.

There's probably other ways to handle this, but I could not find one that also circumvents your system memory filling up when it runs into a multi-gigabyte logfile.   

## Installation
The configuration file requires the Python YAML library. Pick one: 
``` 
pip install -r requirements.txt
yum install python-yaml
apt install python-yaml
```

##### Configure logparseconfig.yaml
Let's skip YAML templating for now. Create a new check like following:
```
configurations:
    count_http_500:
      logfile: /var/log/httpd/ssl_access_log
      message: Less than 50 HTTP 500 errors in the log file
      critical:
        greaterthan: 50
        message: More than 50 HTTP 500 errors in the log file
```


##### Testing
When you run the check_log script with the -h parameter, it will display a help message and the names of all known tests. If you run the check_log script without any parameters it will then run all tests.

To run just your own script run with the -c parameter. If results are as expected (a nice "OK: Less than 50 HTTP 500 errors in the log file") you can add your check to Nagios, if it has local access. 

##### NRPE  
To run over NRPE, just add the following to your NRPE configuration. 
```
command[chk_http500]=/usr/bin/python /usr/local/check_log.py -c count_http_500
```
The chk_http500 label is the command given to the NRPE configuration in Nagios and refers to the check_http500 entry in the script YAML file. The script will then grab the correct configuration from the YAML config, count through the given log file and parse the results to return a succesful Nagios result.

If you run the script with the -g parameter it will generate an NRPE configuration for all known checks.    
  
