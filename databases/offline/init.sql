drop table fidelity_wifi;
drop table fidelity_wifi_location;
create table fidelity_wifi_location (
    mac macaddr,
    tile_x smallint,
    tile_y smallint,
    num integer,
    center geometry,
    bounds box2d,
    tsbounds tsrange
);

create unique index on fidelity_wifi_location (mac, tile_x, tile_y);

drop table fidelity_wifi_radio;
create table fidelity_wifi_radio (
    mac macaddr,
    caps text,
    ssid text,
    freq smallint
);
create unique index on fidelity_wifi_radio (mac, caps, ssid, freq);

create or replace function insert_wifi(
                                        a_mac macaddr,
                                        longitude double precision,
                                        latitude double precision,
                                        ts timestamp,
                                        a_caps text,
                                        a_ssid text,
                                        a_freq smallint
                                    )
returns void
as $$
declare
    a_tile_x smallint;
    a_tile_y smallint;
    point geometry;
    cur_row fidelity_wifi_location%ROWTYPE;
begin
    a_tile_x = trunc((longitude) / 360.0 * (2^16));
    a_tile_y = trunc((latitude) / 180.0 * (2^16));
    point = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);
    SELECT * into cur_row from fidelity_wifi_location f where f.mac = a_mac and f.tile_x = a_tile_x and f.tile_y = a_tile_y;
    if found then
        if cur_row.num = 1 and cur_row.center = point then
            return;
        end if;
        if point && cur_row.bounds and (ts < (upper(cur_row.tsbounds) + interval '1 day')) then
            return;
        end if;

        -- lon, lat
        cur_row.center = ST_SetSRID(ST_MakePoint(
            (ST_X(cur_row.center) * cur_row.num + longitude) / (cur_row.num + 1),
            (ST_Y(cur_row.center) * cur_row.num + latitude)  / (cur_row.num + 1)
            ), 4326);

        -- bounds
        cur_row.bounds = ST_MakeBox2D(
            ST_MakePoint(least(ST_X(point), ST_XMin(cur_row.bounds)), least(ST_Y(point), ST_YMin(cur_row.bounds))),
            ST_MakePoint(greatest(ST_X(point), ST_XMax(cur_row.bounds)), greatest(ST_Y(point), ST_YMax(cur_row.bounds)))
        );

        -- timestamp
        cur_row.tsbounds = ('['||least(lower(cur_row.tsbounds),ts)||', '||greatest(upper(cur_row.tsbounds),ts)||']')::tsrange;

        update fidelity_wifi_location f set
            num = f.num + 1,
            center = cur_row.center,
            bounds = cur_row.bounds,
            tsbounds = cur_row.tsbounds
        where f.mac = a_mac and f.tile_x = a_tile_x and f.tile_y = a_tile_y;
    else
        insert into fidelity_wifi_location (mac, tile_x, tile_y, num, center, bounds, tsbounds)
            values (a_mac, a_tile_x, a_tile_y, 1, point, point, ('['||ts||', '||ts||']')::tsrange);
    end if;
    insert into fidelity_wifi_radio (mac, caps, ssid, freq) values (a_mac, a_caps, a_ssid, a_freq);
exception
    when others then
end
$$ language plpgsql;



drop table fidelity_ip_location;
create table fidelity_ip_location (
    ip cidr,
    num integer,
    center geometry,
    bounds box2d,
    tsbounds tsrange
);

create unique index on fidelity_ip_location (ip);

create or replace function insert_ip(
                                        ip inet,
                                        longitude double precision,
                                        latitude double precision,
                                        ts timestamp
                                    )
returns void
as $$
declare
    mask smallint;
    point geometry;
    ip_masked cidr;
    cur_row fidelity_ip_location%ROWTYPE;
begin
    point = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);
    for mask in reverse 28..16 loop
        ip_masked = set_masklen(ip::cidr, mask);

        SELECT * into cur_row from fidelity_ip_location f where f.ip = ip_masked;
        if found then
            if cur_row.num > 10 and point && cur_row.bounds and (ts > (lower(cur_row.tsbounds) - interval '1 day') and ts < (upper(cur_row.tsbounds) + interval '1 day')) then
                return;
            end if;

            -- lon, lat
            cur_row.center = ST_SetSRID(ST_MakePoint(
                (ST_X(cur_row.center) * cur_row.num + longitude) / (cur_row.num + 1),
                (ST_Y(cur_row.center) * cur_row.num + latitude)  / (cur_row.num + 1)
                ), 4326);

            -- bounds
            cur_row.bounds = ST_MakeBox2D(
                ST_MakePoint(least(ST_X(point), ST_XMin(cur_row.bounds)), least(ST_Y(point), ST_YMin(cur_row.bounds))),
                ST_MakePoint(greatest(ST_X(point), ST_XMax(cur_row.bounds)), greatest(ST_Y(point), ST_YMax(cur_row.bounds)))
            );

            -- timestamp
            cur_row.tsbounds = ('['||least(lower(cur_row.tsbounds),ts)||', '||greatest(upper(cur_row.tsbounds),ts)||']')::tsrange;

            update fidelity_ip_location f set
                num = f.num + 1,
                center = cur_row.center,
                bounds = cur_row.bounds,
                tsbounds = cur_row.tsbounds
            where f.ip = ip_masked;

        else
            insert into fidelity_ip_location (ip, num, center, bounds, tsbounds)
                values (ip_masked, 1, point, point, ('['||ts||', '||ts||']')::tsrange);

        end if;
    end loop;
exception
    WHEN unique_violation THEN
end
$$ language plpgsql;

