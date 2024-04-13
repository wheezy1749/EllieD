import socket
import subprocess
from varname import nameof
from client_ips import *

def ping(host, name=None):
    if name is None:
        name = host
    try:
        socket.inet_aton(host)
    except socket.error:
        print("IP {host} : {name} is OFFLINE!")

    command = ['ping', '-c', '1', host]
    result = subprocess.run(command, stdout=subprocess.PIPE)
    output = result.stdout.decode('utf8')
    if "Request timed out." in output or "100% packet loss" in output or "Name or service not known" in output:
        print(f"IP {host} : {name} is OFFLINE!")
    else:
        print(f"IP {host} : {name} is ONLINE!")

for name, ip in CLIENT_IPS.items():
    ping(ip, name)
