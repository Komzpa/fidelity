# This module is using system ARP table to detect device mac addresses


def get_state():
    macs = set()
    with open("/proc/net/arp") as arp_table:
        next(arp_table)
        for line in arp_table:
            line = line.strip().split()
            macs.add(line[3])
    macs.discard("00:00:00:00:00:00")
    wifi = [{"mac": i, "ss": -30} for i in macs]
    return {"wifi": wifi}


if __name__ == "__main__":
    print(get_state())
