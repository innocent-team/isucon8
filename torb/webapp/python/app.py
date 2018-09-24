import MySQLdb
import MySQLdb.cursors
import flask
import functools
import os
import pathlib
import copy
import json
import random
import subprocess
from io import StringIO
import csv
from datetime import datetime, timezone
import hashlib


base_path = pathlib.Path(__file__).resolve().parent.parent
static_folder = base_path / 'static'
icons_folder = base_path / 'public' / 'icons'


class CustomFlask(flask.Flask):
    jinja_options = flask.Flask.jinja_options.copy()
    jinja_options.update(dict(
        block_start_string='(%',
        block_end_string='%)',
        variable_start_string='((',
        variable_end_string='))',
        comment_start_string='(#',
        comment_end_string='#)',
    ))


app = CustomFlask(__name__, static_folder=str(static_folder), static_url_path='')
app.config['SECRET_KEY'] = 'tagomoris'


if not os.path.exists(str(icons_folder)):
    os.makedirs(str(icons_folder))


def make_base_url(request):
    return request.url_root[:-1]


@app.template_filter('tojsonsafe')
def tojsonsafe(target):
    return json.dumps(target).replace("+", "\\u002b").replace("<", "\\u003c").replace(">", "\\u003e")


def jsonify(target):
    return json.dumps(target)


def res_error(error="unknown", status=500):
    return (jsonify({"error": error}), status)


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not get_login_user():
            return res_error('login_required', 401)
        return f(*args, **kwargs)
    return wrapper


def admin_login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not get_login_administrator():
            return res_error('admin_login_required', 401)
        return f(*args, **kwargs)
    return wrapper


def dbh():
    if hasattr(flask.g, 'db'):
        return flask.g.db
    flask.g.db = MySQLdb.connect(
        host=os.environ['DB_HOST'],
        port=3306,
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASS'],
        database=os.environ['DB_DATABASE'],
        charset='utf8mb4',
        cursorclass=MySQLdb.cursors.DictCursor,
        autocommit=True,
    )
    cur = flask.g.db.cursor()
    cur.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
    return flask.g.db


@app.teardown_appcontext
def teardown(error):
    if hasattr(flask.g, "db"):
        flask.g.db.close()

rank_price = {'S': 5000, 'A': 3000, 'B': 1000, 'C': 0}
rank_count = {'S': 50, 'A': 150, 'B': 300, 'C': 500}
rank_total = 1000
_sheets = None

def sheets():
    global _sheets
    if _sheets:
        return _sheets

    cur = dbh().cursor()
    cur.execute("SELECT * FROM sheets ORDER BY `rank`, num")
    _sheets = cur.fetchall()
    return _sheets


def calculate_rank(sheet_id):
    """
    case
      when sheet_id between 1 and 50 then 'S'
      when sheet_id between 51 and 200 then 'A'
      when sheet_id between 201 and 500 then 'B'
      else 'C'
    end
    """
    if 1 <= sheet_id <= 50:
        return 'S'
    elif 51 <= sheet_id <= 200:
        return 'A'
    elif 201 <= sheet_id <= 500:
        return 'B'
    elif 501 <= sheet_id <= 1000:
        return 'C'
    else:
        raise Exception("Invalid sheet_id: {}".format(sheet_id))


def event_exist(event_id):
    cur = dbh().cursor()
    cur.execute("SELECT 1 FROM events WHERE id = %s", [event_id])
    event = cur.fetchone()
    return event

def event_exist_and_public(event_id):
    cur = dbh().cursor()
    cur.execute("SELECT id, public_fg FROM events WHERE id = %s", [event_id])
    event = cur.fetchone()

    if not event:
        return False

    return bool(event['public_fg'])


def get_events(only_public=False):
    conn = dbh()
    conn.autocommit(False)
    cur = conn.cursor()
    try:
        if only_public:
            cur.execute("SELECT * FROM events WHERE public_fg = 1 ORDER BY id ASC")
        else:
            cur.execute("SELECT * FROM events ORDER BY id ASC")
        rows = cur.fetchall()
        event_ids = [row['id'] for row in rows]
        events = []
        for event_id in event_ids:
            event = get_event(event_id, with_detail=False)
            events.append(event)
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        raise e
    return events


def get_event(event_id, login_user_id=None, with_detail=True):
    cur = dbh().cursor()
    cur.execute("SELECT * FROM events WHERE id = %s", [event_id])
    event = cur.fetchone()
    if not event: return None

    event["total"] = rank_total
    event["remains"] = 0
    event["sheets"] = {}

    for rank in rank_price:
        rank_info = {
            'total': rank_count[rank], 'remains': rank_count[rank], 'detail': [], 'price': event['price'] + rank_price[rank]
        }
        if with_detail:
            rank_info['detail'] = []
        event["sheets"][rank] = rank_info

    cur.execute("""
    select r.sheet_id, r.user_id, r.reserved_at
    from reservations r
    where
        r.canceled_at is null
        and r.event_id = %s
    group by 1
    having r.reserved_at = min(r.reserved_at)
    order by 1
    """, [event_id])
    reserved_sheets = cur.fetchall()

    event['remains'] = rank_total

    if with_detail:
        for sheet in sheets():
            event['sheets'][sheet['rank']]['detail'].append(copy.copy(sheet))

    for reserved_sheet in reserved_sheets:
        sheet_id = reserved_sheet['sheet_id']
        sheet_index = sheet_id - 1
        sheet_num_index = sheets()[sheet_index]['num'] - 1
        rank = calculate_rank(sheet_id)
        event['sheets'][rank]['remains'] -= 1
        event['remains'] -= 1

        if with_detail:
            if login_user_id and reserved_sheet['user_id'] == login_user_id:
                event['sheets'][rank]['detail'][sheet_num]['mine'] = True
            event['sheets'][rank]['detail'][sheet_num_index]['reserved'] = True
            event['sheets'][rank]['detail'][sheet_num_index]['reserved_at'] = int(reserved_sheet['reserved_at'].replace(tzinfo=timezone.utc).timestamp())

    event['public'] = True if event['public_fg'] else False
    event['closed'] = True if event['closed_fg'] else False
    del event['public_fg']
    del event['closed_fg']
    return event

def sanitize_event(event):
    sanitized = copy.copy(event)
    del sanitized['price']
    del sanitized['public']
    del sanitized['closed']
    return sanitized


def get_login_user():
    if "user_id" not in flask.session:
        return None
    cur = dbh().cursor()
    user_id = flask.session['user_id']
    cur.execute("SELECT id, nickname FROM users WHERE id = %s", [user_id])
    return cur.fetchone()


def get_login_administrator():
    if "administrator_id" not in flask.session:
        return None
    cur = dbh().cursor()
    administrator_id = flask.session['administrator_id']
    cur.execute("SELECT id, nickname FROM administrators WHERE id = %s", [administrator_id])
    return cur.fetchone()


def validate_rank(rank):
    return rank in set('SABC')


def render_report_csv(reports):
    keys = ["reservation_id", "event_id", "rank", "num", "price", "user_id", "sold_at", "canceled_at"]

    body = []
    body.append(keys)
    for report in reports:
        body.append([report[key] for key in keys])

    f = StringIO()
    writer = csv.writer(f)
    writer.writerows(body)
    res = flask.make_response()
    res.data = f.getvalue()
    res.headers['Content-Type'] = 'text/csv'
    res.headers['Content-Disposition'] = 'attachment; filename=report.csv'
    return res


@app.route('/')
def get_index():
    user = get_login_user()
    events = []
    for event in get_events(True):
        events.append(sanitize_event(event))
    return flask.render_template('index.html', user=user, events=events, base_url=make_base_url(flask.request))


@app.route('/initialize')
def get_initialize():
    subprocess.call(["../../db/init.sh"])
    return ('', 204)


@app.route('/api/users', methods=['POST'])
def post_users():
    nickname = flask.request.json['nickname']
    login_name = flask.request.json['login_name']
    password = flask.request.json['password']

    conn = dbh()
    conn.autocommit(False)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE login_name = %s", [login_name])
        duplicated = cur.fetchone()
        if duplicated:
            conn.rollback()
            return res_error('duplicated', 409)
        cur.execute(
            "INSERT INTO users (login_name, pass_hash, nickname) VALUES (%s, SHA2(%s, 256), %s)",
            [login_name, password, nickname])
        user_id = cur.lastrowid
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        print(e)
        return res_error()
    return (jsonify({"id": user_id, "nickname": nickname}), 201)


@app.route('/api/users/<int:user_id>')
@login_required
def get_users(user_id):
    cur = dbh().cursor()
    cur.execute('SELECT id, nickname FROM users WHERE id = %s', [user_id])
    user = cur.fetchone()
    if user['id'] != get_login_user()['id']:
        return ('', 403)

    cur.execute("""
        SELECT r.*, s.rank AS sheet_rank, s.num AS sheet_num,
               e.title AS title, e.price AS price,
               e.public_fg AS public_fg, e.closed_fg AS closed_fg
        FROM reservations r
        INNER JOIN sheets s ON s.id = r.sheet_id
        INNER JOIN events e ON e.id = r.event_id
        WHERE
            r.user_id = %s
        ORDER BY IFNULL(r.canceled_at, r.reserved_at)
        DESC LIMIT 5
        """,
        [user['id']])
    recent_reservations = []
    for row in cur.fetchall():
        event = {
            'id': int(row['event_id']),
            'title': row['title'],
            'price': int(row['price']),
            'public': True if row['public_fg'] else False,
            'closed': True if row['closed_fg'] else False,
        }

        if row['canceled_at']:
            canceled_at = int(row['canceled_at'].replace(tzinfo=timezone.utc).timestamp())
        else:
            canceled_at = None

        price = rank_price[row['sheet_rank']] + event['price']
 
        recent_reservations.append({
            "id": int(row['id']),
            "event": event,
            "sheet_rank": row['sheet_rank'],
            "sheet_num": int(row['sheet_num']),
            "price": int(price),
            "reserved_at": int(row['reserved_at'].replace(tzinfo=timezone.utc).timestamp()),
            "canceled_at": canceled_at,
        })

    user['recent_reservations'] = recent_reservations
    cur.execute("""
        SELECT IFNULL(SUM(e.price + s.price), 0) AS total_price
        FROM reservations r
        INNER JOIN sheets s
        ON s.id = r.sheet_id
        INNER JOIN events e
        ON e.id = r.event_id
        WHERE
            r.user_id = %s
            AND r.canceled_at IS NULL
        """,
        [user['id']])
    row = cur.fetchone()
    user['total_price'] = int(row['total_price'])

    cur.execute("""
        SELECT event_id
        FROM reservations
        WHERE user_id = %s
        GROUP BY event_id
        ORDER BY MAX(IFNULL(canceled_at, reserved_at))
        DESC LIMIT 5
        """,
        [user['id']])
    rows = cur.fetchall()
    recent_events = []
    for row in rows:
        event = get_event(row['event_id'], with_detail=False)
        recent_events.append(event)
    user['recent_events'] = recent_events

    return jsonify(user)


@app.route('/api/actions/login', methods=['POST'])
def post_login():
    login_name = flask.request.json['login_name']
    password = flask.request.json['password']

    cur = dbh().cursor()

    cur.execute('SELECT * FROM users WHERE login_name = %s', [login_name])
    user = cur.fetchone()
    pass_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
    if not user or pass_hash != user['pass_hash']:
        return res_error("authentication_failed", 401)

    flask.session['user_id'] = user["id"]
    user = get_login_user()
    return flask.jsonify(user)


@app.route('/api/actions/logout', methods=['POST'])
@login_required
def post_logout():
    flask.session.pop('user_id', None)
    return ('', 204)


@app.route('/api/events')
def get_events_api():
    events = []
    for event in get_events(True):
        events.append(sanitize_event(event))
    return jsonify(events)


@app.route('/api/events/<int:event_id>')
def get_events_by_id(event_id):
    user = get_login_user()
    if user: event = get_event(event_id, user['id'])
    else: event = get_event(event_id)

    if not event or not event["public"]:
        return res_error("not_found", 404)

    event = sanitize_event(event)
    return jsonify(event)


@app.route('/api/events/<int:event_id>/actions/reserve', methods=['POST'])
@login_required
def post_reserve(event_id):
    rank = flask.request.json["sheet_rank"]

    user = get_login_user()

    if not event_exist_and_public(event_id):
        return res_error("invalid_event", 404)
    if not validate_rank(rank):
        return res_error("invalid_rank", 400)

    sheet = None
    reservation_id = 0

    conn =  dbh()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, num FROM sheets
        WHERE
            id NOT IN (
                SELECT sheet_id
                FROM reservations
                WHERE
                    event_id = %s
                    AND canceled_at IS NULL
                FOR UPDATE
            )
            AND `rank` =%s
        """,
        [event_id, rank])
    sheets = cur.fetchall()
    sheet = random.choice(sheets)
    if not sheet:
        return res_error("sold_out", 409)
    try:
        conn.autocommit(False)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO reservations (event_id, sheet_id, user_id, reserved_at) VALUES (%s, %s, %s, %s)",
            [event_id, sheet['id'], user['id'], datetime.utcnow().strftime("%F %T.%f")])
        reservation_id = cur.lastrowid
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        print(e)

    content = jsonify({
        "id": reservation_id,
        "sheet_rank": rank,
        "sheet_num": sheet['num']})
    return flask.Response(content, status=202, mimetype='application/json')


@app.route('/api/events/<int:event_id>/sheets/<rank>/<int:num>/reservation', methods=['DELETE'])
@login_required
def delete_reserve(event_id, rank, num):
    user = get_login_user()

    if not event_exist_and_public(event_id):
        return res_error("invalid_event", 404)
    if not validate_rank(rank):
        return res_error("invalid_rank", 404)

    cur = dbh().cursor()
    cur.execute('SELECT id FROM sheets WHERE `rank` = %s AND num = %s', [rank, num])
    sheet = cur.fetchone()
    if not sheet:
        return res_error("invalid_sheet", 404)

    try:
        conn = dbh()
        conn.autocommit(False)
        cur = conn.cursor()

        cur.execute("""
            SELECT id, user_id, event_id, reserved_at FROM reservations
            WHERE
                event_id = %s
                AND sheet_id = %s
                AND canceled_at IS NULL
            GROUP BY event_id
            HAVING reserved_at = MIN(reserved_at)
            FOR UPDATE
            """,
            [event_id, sheet['id']])
        reservation = cur.fetchone()

        if not reservation:
            conn.rollback()
            return res_error("not_reserved", 400)
        if reservation['user_id'] != user['id']:
            conn.rollback()
            return res_error("not_permitted", 403)

        cur.execute(
            "UPDATE reservations SET canceled_at = %s WHERE id = %s",
            [datetime.utcnow().strftime("%F %T.%f"), reservation['id']])
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        print(e)
        return res_error()

    return flask.Response(status=204)


@app.route('/admin/')
def get_admin():
    administrator = get_login_administrator()
    if administrator: events=get_events()
    else: events={}
    return flask.render_template('admin.html', administrator=administrator, events=events, base_url=make_base_url(flask.request))


@app.route('/admin/api/actions/login', methods=['POST'])
def post_adin_login():
    login_name = flask.request.json['login_name']
    password = flask.request.json['password']

    cur = dbh().cursor()

    cur.execute('SELECT * FROM administrators WHERE login_name = %s', [login_name])
    administrator = cur.fetchone()
    cur.execute('SELECT SHA2(%s, 256) AS pass_hash', [password])
    pass_hash = cur.fetchone()

    if not administrator or pass_hash['pass_hash'] != administrator['pass_hash']:
        return res_error("authentication_failed", 401)

    flask.session['administrator_id'] = administrator['id']
    administrator = get_login_administrator()
    return jsonify(administrator)


@app.route('/admin/api/actions/logout', methods=['POST'])
@admin_login_required
def get_admin_logout():
    flask.session.pop('administrator_id', None)
    return ('', 204)


@app.route('/admin/api/events')
@admin_login_required
def get_admin_events_api():
    return jsonify(get_events())


@app.route('/admin/api/events', methods=['POST'])
@admin_login_required
def post_admin_events_api():
    title = flask.request.json['title']
    public = flask.request.json['public']
    price = flask.request.json['price']

    conn = dbh()
    conn.autocommit(False)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO events (title, public_fg, closed_fg, price) VALUES (%s, %s, 0, %s)",
            [title, public, price])
        event_id = cur.lastrowid
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        print(e)
    return jsonify(get_event(event_id))


@app.route('/admin/api/events/<int:event_id>')
@admin_login_required
def get_admin_events_by_id(event_id):
    event = get_event(event_id)
    if not event:
        return res_error("not_found", 404)
    return jsonify(event)


@app.route('/admin/api/events/<int:event_id>/actions/edit', methods=['POST'])
@admin_login_required
def post_event_edit(event_id):
    public = flask.request.json['public'] if 'public' in flask.request.json.keys() else False
    closed = flask.request.json['closed'] if 'closed' in flask.request.json.keys() else False
    if closed: public = False

    event = get_event(event_id)
    if not event:
        return res_error("not_found", 404)

    if event['closed']:
        return res_error('cannot_edit_closed_event', 400)
    elif event['public'] and closed:
        return res_error('cannot_close_public_event', 400)

    conn = dbh()
    conn.autocommit(False)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE events SET public_fg = %s, closed_fg = %s WHERE id = %s",
            [public, closed, event['id']])
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
    return jsonify(get_event(event_id))


@app.route('/admin/api/reports/events/<int:event_id>/sales')
@admin_login_required
def get_admin_event_sales(event_id):
    if not event_exist(event_id):
        return res_error("not_found", 404)

    cur = dbh().cursor()
    for i in range(3):
        try:
            cur.execute('select price from events where id = %s', [event_id])
            event_price = cur.fetchone()['price']
            reservations = cur.execute('''
                SELECT r.*, s.rank AS sheet_rank, s.num AS sheet_num, s.price AS sheet_price, %s AS event_price
                FROM reservations r
                INNER JOIN sheets s ON s.id = r.sheet_id
                WHERE
                    r.event_id = %s
                ORDER BY reserved_at ASC
                FOR UPDATE''',
                [event_price, event_id])
            reservations = cur.fetchall()
            break
        except MySQLdb.Error as e:
            if i == 2:
                raise e
    reports = []

    for reservation in reservations:
        if reservation['canceled_at']:
            canceled_at = reservation['canceled_at'].isoformat()+"Z"
        else: canceled_at = ''
        reports.append({
            "reservation_id": reservation['id'],
            "event_id":       event_id,
            "rank":           reservation['sheet_rank'],
            "num":            reservation['sheet_num'],
            "user_id":        reservation['user_id'],
            "sold_at":        reservation['reserved_at'].isoformat()+"Z",
            "canceled_at":    canceled_at,
            "price":          reservation['event_price'] + reservation['sheet_price'],
        })

    return render_report_csv(reports)


@app.route('/admin/api/reports/sales')
@admin_login_required
def get_admin_sales():
    cur = dbh().cursor()
    reservations = cur.execute('''
        SELECT
            r.*,
            s.rank AS sheet_rank, s.num AS sheet_num, s.price AS sheet_price,
            e.id AS event_id, e.price AS event_price
        FROM reservations r
        INNER JOIN sheets s
        ON s.id = r.sheet_id
        INNER JOIN events e
        ON e.id = r.event_id
        ORDER BY reserved_at ASC
        FOR UPDATE
    ''')
    reservations = cur.fetchall()

    reports = []
    for reservation in reservations:
        if reservation['canceled_at']:
            canceled_at = reservation['canceled_at'].isoformat()+"Z"
        else: canceled_at = ''
        reports.append({
            "reservation_id": reservation['id'],
            "event_id":       reservation['event_id'],
            "rank":           reservation['sheet_rank'],
            "num":            reservation['sheet_num'],
            "user_id":        reservation['user_id'],
            "sold_at":        reservation['reserved_at'].isoformat()+"Z",
            "canceled_at":    canceled_at,
            "price":          reservation['event_price'] + reservation['sheet_price'],
        })
    return render_report_csv(reports)


if __name__ == "__main__":
    import bjoern
    bjoern.run(app, "0.0.0.0", 8080)
    # app.run(port=8080, debug=True, threaded=True)
