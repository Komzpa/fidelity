try:
    import psycopg2

    DatabaseError = psycopg2.Error
except ImportError:
    psycopg2 = None
    DatabaseError = RuntimeError

database = "dbname=gis user=gis"

connection = None
cursor = None


def _cursor():
    global connection, cursor
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is required for the PostgreSQL backend")
    if cursor is None:
        connection = psycopg2.connect(database)
        connection.autocommit = True
        cursor = connection.cursor()
    return cursor


def savewifi(ap, pos):
    freq = ap.get("freq", 0)
    if freq > 32767 or freq < 0:
        freq = 0

    _cursor().execute(
        (
            "select insert_wifi(%s::macaddr, %s, %s, to_timestamp(%s)::timestamp, "
            "%s::text, %s::text, %s::smallint)"
        ),
        (
            ap["mac"],
            pos["longitude"],
            pos["latitude"],
            pos["time"],
            ap.get("caps", "").strip(),
            ap.get("ssid", "").strip(),
            freq,
        ),
    )


def saveip(ip, pos):
    _cursor().execute(
        "select insert_ip(%s::inet, %s, %s, to_timestamp(%s)::timestamp)",
        (ip, pos["longitude"], pos["latitude"], pos["time"]),
    )


def get_location(req={}):
    pos = {}
    if req.get("ip"):
        try:
            db_cursor = _cursor()
            db_cursor.execute(
                """
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
                limit 1;""",
                {"ip": req["ip"]},
            )
            for row in db_cursor:
                pos = {
                    "type": "ip",
                    "latitude": row[1],
                    "longitude": row[0],
                    "accuracy": max(500.1, 14.0 * (row[2] ** 2.7)),
                }
        except (RuntimeError, DatabaseError):
            return False
    if pos:
        return {"position": pos, "service": "postgres db"}
    return False
