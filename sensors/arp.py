import subprocess

# This module is using system ARP table to detect device mac addresses

def get_state():
    arp_table = open('/proc/net/arp', 'r')
    macs = set()
    arp_table.next()
    for line in arp_table:
        line = line.strip().split()
    macs.add(line[3])
    macs.discard('00:00:00:00:00:00')
    wifi = [{'mac':i, 'ss':-30} for i in macs]
    return {"wifi": wifi}

if __name__ == "__main__":
    print get_state()
