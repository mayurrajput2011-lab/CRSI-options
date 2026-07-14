from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import sqlite3, os, csv, io
from datetime import datetime

app = Flask(__name__, static_folder='.')
CORS(app)
DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        instrument TEXT DEFAULT 'NIFTY',
        candle_type TEXT, candle_score TEXT, candle_note TEXT,
        possible_move TEXT, move_value TEXT,
        atr_direction TEXT, crsi_direction TEXT, crsi_level TEXT,
        market_structure TEXT, market_regime TEXT,
        htf_fvg TEXT, order_block TEXT, vix_direction TEXT,
        grade TEXT, trade_decision TEXT, option_type TEXT,
        entry_reason TEXT, screenshot TEXT,
        strategy_name TEXT,
        next_day_move TEXT, move_d2 TEXT, move_d3 TEXT, move_d4 TEXT, move_d5 TEXT,
        nearest_obstacle TEXT, reason_failure TEXT,
        result TEXT, pnl TEXT,
        exit_reason TEXT, comments TEXT
    )''')
    conn.commit()
    conn.execute('''CREATE TABLE IF NOT EXISTS day_labels (
        trade_date TEXT PRIMARY KEY,
        label TEXT
    )''')
    conn.commit()
    # Migrate existing DBs — add new columns if missing
    existing = {row[1] for row in conn.execute('PRAGMA table_info(trades)').fetchall()}
    for col, typ in [('move_d2','TEXT'),('move_d3','TEXT'),('move_d4','TEXT'),('move_d5','TEXT'),('nearest_obstacle','TEXT'),('reason_failure','TEXT'),('strategy_name','TEXT'),
                     ('atr_d1','TEXT'),('crsi_d1','TEXT'),('atr_d2','TEXT'),('crsi_d2','TEXT'),
                     ('atr_d3','TEXT'),('crsi_d3','TEXT'),('atr_d4','TEXT'),('crsi_d4','TEXT'),
                     ('atr_d5','TEXT'),('crsi_d5','TEXT'),
                     ('main_entry_price','TEXT'),('main_cost','TEXT'),('option_amount','TEXT'),
                     ('hedge_type','TEXT'),('hedge_entry_price','TEXT'),('hedge_cost','TEXT'),
                     ('main_exit_price','TEXT'),('main_exit_cost','TEXT'),
                     ('hedge_exit_price','TEXT'),('hedge_exit_cost','TEXT')]:
        if col not in existing:
            try: conn.execute(f'ALTER TABLE trades ADD COLUMN {col} {typ}'); conn.commit(); print(f'[DB] Added: {col}')
            except Exception as e: print(f'[DB] Skip {col}: {e}')
    conn.close()

@app.route('/')
def index(): return send_from_directory('.', 'index.html')

@app.route('/api/trades', methods=['GET'])
def get_trades():
    conn = get_db()
    trades = conn.execute('SELECT * FROM trades ORDER BY trade_date DESC, id DESC').fetchall()
    conn.close()
    return jsonify([dict(t) for t in trades])

@app.route('/api/trades', methods=['POST'])
def add_trade():
    data = request.json
    data['created_at'] = datetime.now().isoformat()
    conn = get_db()
    cols = ', '.join(data.keys())
    placeholders = ', '.join(['?' for _ in data])
    conn.execute(f'INSERT INTO trades ({cols}) VALUES ({placeholders})', list(data.values()))
    conn.commit()
    last = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    trade = conn.execute('SELECT * FROM trades WHERE id=?', (last,)).fetchone()
    conn.close()
    return jsonify(dict(trade)), 201

@app.route('/api/trades/<int:trade_id>', methods=['PUT'])
def update_trade(trade_id):
    data = request.json
    conn = get_db()
    sets = ', '.join([f'{k}=?' for k in data.keys()])
    conn.execute(f'UPDATE trades SET {sets} WHERE id=?', list(data.values()) + [trade_id])
    conn.commit()
    trade = conn.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone()
    conn.close()
    return jsonify(dict(trade))

@app.route('/api/trades/<int:trade_id>', methods=['DELETE'])
def delete_trade(trade_id):
    conn = get_db()
    conn.execute('DELETE FROM trades WHERE id=?', (trade_id,))
    conn.commit(); conn.close()
    return jsonify({'deleted': trade_id})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    rows = conn.execute('SELECT * FROM trades WHERE result IS NOT NULL AND result != ""').fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    if not trades: return jsonify({})

    total  = len(trades)
    wins   = sum(1 for t in trades if t['result'] == 'Profit')
    losses = sum(1 for t in trades if t['result'] == 'Loss')
    win_rate = round((wins / total) * 100, 1) if total else 0

    def breakdown(key):
        d = {}
        for t in trades:
            g = t.get(key) or 'Unknown'
            d.setdefault(g, {'total': 0, 'wins': 0})
            d[g]['total'] += 1
            if t['result'] == 'Profit': d[g]['wins'] += 1
        return d

    def parse_final_move(t):
        # A trade resolves on exactly ONE of D1-D5 (whichever day it hit
        # target/stoploss/close) — the others are left blank. Check from the
        # last day backwards so we don't miss trades that closed on D4/D5.
        for field in ('move_d5', 'move_d4', 'move_d3', 'move_d2', 'next_day_move'):
            raw = (t.get(field) or '').replace('%', '').strip()
            if not raw:
                continue
            try:
                return float(raw)
            except ValueError:
                continue
        return None

    all_moves, win_moves, loss_moves = [], [], []
    for t in trades:
        m = parse_final_move(t)
        if m is None:
            continue
        all_moves.append(m)
        if t['result'] == 'Profit':  win_moves.append(abs(m))
        elif t['result'] == 'Loss':  loss_moves.append(abs(m))

    total_gained = round(sum(win_moves), 2)
    total_lost   = round(sum(loss_moves), 2)
    net_move     = round(total_gained - total_lost, 2)
    avg_win      = round(sum(win_moves) / len(win_moves), 2) if win_moves else 0
    avg_loss     = round(sum(loss_moves) / len(loss_moves), 2) if loss_moves else 0
    rr           = round(avg_win / avg_loss, 2) if avg_loss else 0

    return jsonify({
        'total': total, 'wins': wins, 'losses': losses, 'win_rate': win_rate,
        'grade_stats':     breakdown('grade'),
        'candle_stats':    breakdown('candle_type'),
        'atr_stats':       breakdown('atr_direction'),
        'structure_stats': breakdown('market_structure'),
        'avg_move':        round(sum(all_moves) / len(all_moves), 2) if all_moves else 0,
        'trades_gt1pct':   sum(1 for m in all_moves if abs(m) >= 1.0),
        'total_gained':    total_gained,
        'total_lost':      total_lost,
        'net_move':        net_move,
        'avg_win':         avg_win,
        'avg_loss':        avg_loss,
        'rr':              rr,
        'win_count':       len(win_moves),
        'loss_count':      len(loss_moves),
    })

@app.route('/api/day-labels', methods=['GET'])
def get_day_labels():
    conn = get_db()
    rows = conn.execute('SELECT trade_date, label FROM day_labels').fetchall()
    conn.close()
    return jsonify({r['trade_date']: r['label'] for r in rows})

@app.route('/api/day-labels/<date>', methods=['PUT'])
def set_day_label(date):
    label = (request.json or {}).get('label', '').strip()
    conn = get_db()
    if label:
        conn.execute('INSERT INTO day_labels (trade_date, label) VALUES (?, ?) '
                     'ON CONFLICT(trade_date) DO UPDATE SET label=excluded.label', (date, label))
    else:
        conn.execute('DELETE FROM day_labels WHERE trade_date=?', (date,))
    conn.commit(); conn.close()
    return jsonify({'trade_date': date, 'label': label})

@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    conn = get_db()
    trades = conn.execute('SELECT id,trade_date,instrument,strategy_name,candle_type,candle_score,candle_note,possible_move,move_value,atr_direction,crsi_direction,crsi_level,market_structure,market_regime,htf_fvg,order_block,vix_direction,grade,trade_decision,option_type,main_entry_price,main_cost,option_amount,hedge_type,hedge_entry_price,hedge_cost,main_exit_price,main_exit_cost,hedge_exit_price,hedge_exit_cost,entry_reason,next_day_move,move_d2,move_d3,move_d4,move_d5,result,pnl,exit_reason,comments FROM trades ORDER BY trade_date DESC').fetchall()
    conn.close()
    if not trades: return 'No data', 404
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=trades[0].keys())
    writer.writeheader(); writer.writerows([dict(t) for t in trades])
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=nifty_trades.csv'})

if __name__ == '__main__':
    init_db()
    print("\n✅ NIFTY Trade Journal → http://localhost:5050\n")
    app.run(debug=False, port=5050)
