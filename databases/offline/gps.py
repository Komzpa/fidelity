import time

def get_location(req = {}, timestamp = None):
    device_time = req.get("gps",req.get("clock",{})).get("currentTime", time.time())
    if not timestamp:
        timestamp = time.time()
    try:
        if "gps" in req:
            gps = req["gps"]
            timedelta = min([
                abs(timestamp - gps["time"]/1000),
                abs(device_time - gps["time"]/1000)
            ])
            acc = gps["accuracy"] + ( timedelta * max(gps.get("speed", 6), 6) )
            pos = {"type":"gps", "latitude": gps["latitude"], "longitude": gps["longitude"], "accuracy": acc}
            if "altitude" in gps:
                pos["altitude"] = gps["altitude"]
            return {"position": pos, "service": "gps proxy"}
    except KeyError:
        pass