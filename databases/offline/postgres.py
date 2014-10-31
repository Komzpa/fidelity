import psycopg2
from datetime import datetime

database = "dbname=gis user=gis"

a = psycopg2.connect(database)
a.autocommit = True
cursor = a.cursor()

def savewifi(ap, pos):
    freq = ap.get("freq", 0)
    if freq > 32767 or freq < 0:
        freq = 0

    cursor.execute("select insert_wifi(%s::macaddr, %s, %s, to_timestamp(%s)::timestamp, %s::text, %s::text, %s::smallint)", (ap["mac"],
                                                                                                    pos['longitude'],
                                                                                                    pos['latitude'],
                                                                                                    pos['time'],
                                                                                                    ap.get("caps", "").strip(),
                                                                                                    ap.get("ssid", "").strip(),
                                                                                                    ap.get("freq", 0),
                                                                                                    ))
    #cursor.callproc("insert_wifi", (mac, pos['longitude'], pos['latitude'], pos.get('altitude', -9000.), pos['accuracy'], datetime.fromtimestamp(pos['time'])))

def saveip(ip, pos):
    cursor.execute("select insert_ip(%s::inet, %s, %s, to_timestamp(%s)::timestamp)",  (ip,
                                                                                                    pos['longitude'],
                                                                                                    pos['latitude'],
                                                                                                    pos['time']
                                                                                                    ))


def get_location(req = {}):
    pos = {}
    if req["ip"]:
        cursor.execute("""
            select
                ST_X(center),
                ST_Y(center),
                masklen(ip),
                bounds
            from
                fidelity_ip_location
            where
                ip >= set_masklen(%(ip)s,16) and
                ip < set_masklen(cidr %(ip)s + 65536, 16) and
                ip >> %(ip)s
            order by
                masklen(ip) desc
            limit 1;""", {"ip": req["ip"]})
        for row in cursor:
            pos = {"type":"ip", "latitude": row[1], "longitude": row[0], "accuracy": max(500.1, 14.*(row[2]**2.7))}
    if pos:
        return {"position": pos, "service": "postgres db"}