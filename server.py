from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import sqlite3, os, csv, io
from datetime import datetime
import screener

app = Flask(__name__, static_folder='.')
CORS(app)
DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')

# Bump this string any time server.py or screener.py changes in a way that
# affects behavior. The frontend displays it on the Screener/Backtest
# pages so you can tell at a glance whether the running server actually
# matches the files you were just given — no more guessing whether a
# restart is needed.
APP_VERSION = '2026-07-21-r4-paper-trade-journal-parity'


@app.route('/api/version', methods=['GET'])
def get_version():
    return jsonify({'version': APP_VERSION})

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
    conn.execute('''CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        qty INTEGER DEFAULT 1,
        entry_date TEXT NOT NULL,
        entry_price REAL NOT NULL,
        status TEXT DEFAULT 'OPEN',
        exit_date TEXT,
        exit_price REAL,
        setup_score INTEGER,
        candle_pattern TEXT,
        crsi_zone TEXT,
        atr_direction TEXT,
        universe TEXT,
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    # Migrate paper_trades — add the fuller auto-fetched detail fields so the
    # Paper Trades screen can show the same depth of detail as the manual
    # Journal (candle metrics, CRSI/ATR values, volume, notes, exit note).
    existing_pt = {row[1] for row in conn.execute('PRAGMA table_info(paper_trades)').fetchall()}
    for col, typ in [
        ('candle_all_patterns', 'TEXT'), ('candle_direction', 'TEXT'),
        ('candle_body_pct', 'REAL'), ('candle_strength', 'TEXT'),
        ('candle_signal_type', 'TEXT'),
        ('crsi', 'REAL'), ('atr', 'REAL'), ('atr_pct', 'REAL'),
        ('day_change_pct', 'REAL'), ('volume', 'INTEGER'),
        ('vol_vs_avg20', 'REAL'), ('as_of', 'TEXT'),
        ('notes', 'TEXT'), ('exit_note', 'TEXT'),
    ]:
        if col not in existing_pt:
            try: conn.execute(f'ALTER TABLE paper_trades ADD COLUMN {col} {typ}'); conn.commit(); print(f'[DB] paper_trades added: {col}')
            except Exception as e: print(f'[DB] Skip paper_trades.{col}: {e}')
    # Migrate existing DBs — add new columns if missing
    existing = {row[1] for row in conn.execute('PRAGMA table_info(trades)').fetchall()}
    for col, typ in [('move_d2','TEXT'),('move_d3','TEXT'),('move_d4','TEXT'),('move_d5','TEXT'),('nearest_obstacle','TEXT'),('reason_failure','TEXT'),('strategy_name','TEXT'),('strike_price','TEXT'),
                     ('atr_d1','TEXT'),('crsi_d1','TEXT'),('atr_d2','TEXT'),('crsi_d2','TEXT'),
                     ('atr_d3','TEXT'),('crsi_d3','TEXT'),('atr_d4','TEXT'),('crsi_d4','TEXT'),
                     ('atr_d5','TEXT'),('crsi_d5','TEXT'),
                     ('main_entry_price','TEXT'),('main_cost','TEXT'),('option_amount','TEXT'),
                     ('hedge_type','TEXT'),('hedge_entry_price','TEXT'),('hedge_cost','TEXT'),('hedge_strike_price','TEXT'),
                     ('main_exit_price','TEXT'),('main_exit_cost','TEXT'),
                     ('hedge_exit_price','TEXT'),('hedge_exit_cost','TEXT'),
                     ('hedge_move_d1','TEXT'),('hedge_move_d2','TEXT'),('hedge_move_d3','TEXT'),
                     ('hedge_move_d4','TEXT'),('hedge_move_d5','TEXT'),('pnl_amount','TEXT')]:
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

@app.route('/api/paper-trades', methods=['GET'])
def get_paper_trades():
    """Every open position is marked to market against today's latest close
    each time this is called — so refreshing tomorrow shows updated P&L
    without you having to do anything. This is simulated tracking only; no
    real broker order is placed."""
    conn = get_db()
    rows = conn.execute('SELECT * FROM paper_trades ORDER BY entry_date DESC, id DESC').fetchall()
    conn.close()
    trades = [dict(r) for r in rows]

    open_symbols = sorted({t['symbol'] for t in trades if t['status'] == 'OPEN'})
    quotes = {}
    if open_symbols:
        try:
            quotes = screener.get_quotes(open_symbols)
        except Exception as e:
            print(f'[paper-trades] quote fetch failed: {e}')

    for t in trades:
        direction = 1 if t['side'] == 'BUY' else -1
        if t['status'] == 'OPEN':
            cur = quotes.get(t['symbol'])
            t['current_price'] = cur
            if cur is not None:
                t['pnl'] = round((cur - t['entry_price']) * direction * t['qty'], 2)
                t['pnl_pct'] = round((cur - t['entry_price']) / t['entry_price'] * direction * 100, 2)
            else:
                t['pnl'] = None
                t['pnl_pct'] = None
        else:
            t['current_price'] = t['exit_price']
            if t['exit_price'] is not None:
                t['pnl'] = round((t['exit_price'] - t['entry_price']) * direction * t['qty'], 2)
                t['pnl_pct'] = round((t['exit_price'] - t['entry_price']) / t['entry_price'] * direction * 100, 2)
            else:
                t['pnl'] = None
                t['pnl_pct'] = None

        # Auto-fetched D1-D5 price move after entry — same idea as the
        # manual journal's next_day_move/move_d2..d5 fields, just pulled
        # from real data instead of typed in by hand.
        try:
            t['day_moves'] = screener.get_day_moves(t['symbol'], t['entry_date'], t['entry_price'])
        except Exception as e:
            print(f"[paper-trades] day-move fetch failed for {t['symbol']}: {e}")
            t['day_moves'] = []
    return jsonify(trades)


@app.route('/api/paper-trades', methods=['POST'])
def add_paper_trade():
    """Logs a simulated Buy (long) or Sell (short) at the given price —
    typically the live screener price the person clicked from."""
    data = request.json or {}
    if not all(k in data and data[k] not in (None, '') for k in ('symbol', 'side', 'price')):
        return jsonify({'error': 'symbol, side, and price are required'}), 400
    side = str(data['side']).upper()
    if side not in ('BUY', 'SELL'):
        return jsonify({'error': "side must be 'BUY' or 'SELL'"}), 400

    conn = get_db()
    conn.execute('''INSERT INTO paper_trades
        (symbol, side, qty, entry_date, entry_price, status, setup_score,
         candle_pattern, crsi_zone, atr_direction, universe, created_at,
         candle_all_patterns, candle_direction, candle_body_pct, candle_strength,
         candle_signal_type, crsi, atr, atr_pct, day_change_pct, volume,
         vol_vs_avg20, as_of, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (data['symbol'], side, int(data.get('qty', 1)),
         datetime.now().strftime('%Y-%m-%d'), float(data['price']), 'OPEN',
         data.get('setup_score'), data.get('candle_pattern'), data.get('crsi_zone'),
         data.get('atr_direction'), data.get('universe'), datetime.now().isoformat(),
         data.get('candle_all_patterns'), data.get('candle_direction'),
         data.get('candle_body_pct'), data.get('candle_strength'),
         data.get('candle_signal_type'), data.get('crsi'), data.get('atr'),
         data.get('atr_pct'), data.get('day_change_pct'), data.get('volume'),
         data.get('vol_vs_avg20'), data.get('as_of'), data.get('notes')))
    conn.commit()
    last = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    trade = conn.execute('SELECT * FROM paper_trades WHERE id=?', (last,)).fetchone()
    conn.close()
    return jsonify(dict(trade)), 201


@app.route('/api/paper-trades/<int:trade_id>/close', methods=['PUT'])
def close_paper_trade(trade_id):
    """Closes an open paper trade at either a supplied price or, if none is
    given, today's latest fetched close."""
    data = request.json or {}
    conn = get_db()
    trade = conn.execute('SELECT * FROM paper_trades WHERE id=?', (trade_id,)).fetchone()
    if not trade:
        conn.close()
        return jsonify({'error': 'not found'}), 404

    exit_price = data.get('price')
    if exit_price in (None, ''):
        quotes = screener.get_quotes([trade['symbol']])
        exit_price = quotes.get(trade['symbol'])
    if exit_price is None:
        conn.close()
        return jsonify({'error': 'could not fetch a current price to close at — try again or pass one manually'}), 500

    exit_date = data.get('date') or datetime.now().strftime('%Y-%m-%d')
    exit_note = data.get('note')

    conn.execute('UPDATE paper_trades SET status=?, exit_date=?, exit_price=?, exit_note=? WHERE id=?',
                 ('CLOSED', exit_date, float(exit_price), exit_note, trade_id))
    conn.commit()
    updated = conn.execute('SELECT * FROM paper_trades WHERE id=?', (trade_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))


@app.route('/api/paper-trades/<int:trade_id>/notes', methods=['PUT'])
def update_paper_trade_notes(trade_id):
    """Lets you jot down your own comments/learnings on a paper trade —
    the one thing that isn't auto-fetched — same idea as the Journal's
    Learnings field. Works whether the trade is still OPEN or CLOSED."""
    data = request.json or {}
    conn = get_db()
    trade = conn.execute('SELECT id FROM paper_trades WHERE id=?', (trade_id,)).fetchone()
    if not trade:
        conn.close()
        return jsonify({'error': 'not found'}), 404
    conn.execute('UPDATE paper_trades SET notes=? WHERE id=?', (data.get('notes', ''), trade_id))
    conn.commit()
    updated = conn.execute('SELECT * FROM paper_trades WHERE id=?', (trade_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))


@app.route('/api/paper-trades/<int:trade_id>', methods=['DELETE'])
def delete_paper_trade(trade_id):
    conn = get_db()
    conn.execute('DELETE FROM paper_trades WHERE id=?', (trade_id,))
    conn.commit()
    conn.close()
    return jsonify({'deleted': trade_id})


@app.route('/api/screener/patterns', methods=['GET'])
def screener_patterns():
    """The definitive list of candle pattern names the algorithmic
    classifier can actually produce — used to populate the Backtest page's
    pattern filter so it can never contain a name the engine will never
    match (e.g. the manual New Entry form's hand-rating labels like
    'Strong Bearish (0/4)', which are a different concept entirely)."""
    generic_fallback = ['Strong Bullish', 'Weak Bullish', 'Strong Bearish', 'Weak Bearish']
    patterns = sorted(set(screener.PATTERN_META.keys()) | set(generic_fallback))
    return jsonify({'patterns': patterns})


@app.route('/api/screener/symbols', methods=['GET'])
def screener_symbols():
    """Full list of tradeable symbols + indices, for the Backtest page's
    security picker/autocomplete."""
    return jsonify({
        'stocks': screener.UNIVERSES['nifty50'] + [
            s for s in screener.UNIVERSES['banknifty'] if s not in screener.UNIVERSES['nifty50']
        ] + [
            s for s in screener.UNIVERSES['sensex'] if s not in screener.UNIVERSES['nifty50']
            and s not in screener.UNIVERSES['banknifty']
        ],
        'indices': list(screener.INDICES.keys()),
    })


@app.route('/api/backtest/run', methods=['GET'])
def backtest_run():
    """Replays the screener's filters day-by-day over N years of real
    history for one symbol, simulating: enter on a matching signal, hold
    while favorable, exit immediately on the first adverse close."""
    symbol = request.args.get('symbol', '').strip()
    if not symbol:
        return jsonify({'error': 'symbol is required', 'trades': [], 'summary': None}), 400
    try:
        years = int(request.args.get('years', 3))
    except ValueError:
        years = 3
    years = max(1, min(years, 15))

    clean_only = request.args.get('clean_only', '') in ('1', 'true', 'yes')
    trade_side = request.args.get('trade_side', 'auto')
    if trade_side not in ('auto', 'buy', 'sell'):
        trade_side = 'auto'
    try:
        result = screener.run_backtest(
            symbol, years=years,
            crsi=request.args.get('crsi', 'any'),
            atr=request.args.get('atr', 'any'),
            candle_dir=request.args.get('candle_dir', 'any'),
            candle_pattern=request.args.get('candle_pattern', 'any'),
            clean_only=clean_only,
            trade_side=trade_side,
        )
    except Exception as e:
        return jsonify({'error': str(e), 'trades': [], 'summary': None}), 500

    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@app.route('/api/screener/scan', methods=['GET'])
def screener_scan():
    """Scans the selected universe (Nifty 50 / Bank Nifty / Sensex / Indices /
    All) for CRSI + ATR + candle-pattern setups.
    Cached per-universe for CACHE_TTL_SECONDS — pass ?refresh=1 to force a
    fresh pull, and ?universe=nifty50|banknifty|sensex|indices|all."""
    force_refresh = request.args.get('refresh', '') in ('1', 'true', 'yes')
    universe = request.args.get('universe', 'all')
    try:
        data = screener.get_scan(force_refresh=force_refresh, universe=universe)
    except Exception as e:
        return jsonify({'results': [], 'errors': [str(e)], 'cached': False, 'age_seconds': 0}), 500

    # Attach each result's historical win-rate from YOUR OWN journal — i.e.
    # among your past closed trades that share the same candle pattern and
    # ATR state, what fraction were profitable. This is real personal
    # history, not a statistical guarantee of future results.
    conn = get_db()
    closed = conn.execute(
        "SELECT candle_type, atr_direction, result FROM trades "
        "WHERE result IS NOT NULL AND result != ''"
    ).fetchall()
    conn.close()

    tally = {}
    for row in closed:
        key = (row['candle_type'] or '', row['atr_direction'] or '')
        tally.setdefault(key, {'wins': 0, 'total': 0})
        tally[key]['total'] += 1
        if row['result'] == 'Profit':
            tally[key]['wins'] += 1

    MIN_SAMPLE = 3
    for r in data.get('results', []):
        key = (r.get('candle_pattern') or '', r.get('atr_direction') or '')
        t = tally.get(key)
        if t and t['total'] >= MIN_SAMPLE:
            r['hist_win_rate'] = round(t['wins'] / t['total'] * 100, 1)
            r['hist_sample'] = t['total']
        else:
            r['hist_win_rate'] = None
            r['hist_sample'] = t['total'] if t else 0

    return jsonify(data)


@app.route('/api/screener/lookup', methods=['GET'])
def screener_lookup():
    """Analyzes ONE arbitrary symbol on demand — the manual search bar on the
    Screener page. Not limited to the Nifty50/BankNifty/Sensex universes;
    any valid NSE symbol (or one of the 3 tracked indices) works, since the
    underlying Yahoo Finance ticker is just SYMBOL + '.NS' unless it's a
    known index name."""
    raw = request.args.get('symbol', '').strip()
    if not raw:
        return jsonify({'error': 'symbol is required'}), 400

    # Resolve to the exact display symbol the rest of the app expects —
    # case-insensitive match against known indices, else uppercase the
    # ticker as typed (NSE symbols are always uppercase).
    symbol = raw.upper()
    for idx_name in screener.INDICES:
        if idx_name.upper() == symbol or idx_name.upper().replace(' ', '') == symbol.replace(' ', ''):
            symbol = idx_name
            break

    try:
        scan = screener.run_scan(symbols=[symbol])
    except Exception as e:
        return jsonify({'error': f"Scan error: {e}"}), 500

    if scan['results']:
        result = scan['results'][0]
        try:
            conn = get_db()
            closed = conn.execute(
                "SELECT candle_type, atr_direction, result FROM trades "
                "WHERE result IS NOT NULL AND result != ''").fetchall()
            conn.close()
            MIN_SAMPLE = 3
            matches = [c for c in closed
                       if (c['candle_type'] or '') == (result.get('candle_pattern') or '')
                       and (c['atr_direction'] or '') == (result.get('atr_direction') or '')]
            if len(matches) >= MIN_SAMPLE:
                wins = sum(1 for c in matches if c['result'] == 'Profit')
                result['hist_win_rate'] = round(wins / len(matches) * 100, 1)
                result['hist_sample'] = len(matches)
            else:
                result['hist_win_rate'] = None
                result['hist_sample'] = len(matches)
        except Exception:
            result['hist_win_rate'] = None
            result['hist_sample'] = 0
        return jsonify({'result': result})

    # Surface the actual yfinance / data error to the frontend
    if scan['errors']:
        err = scan['errors'][0]
        # Common case: MultiIndex column error — give friendly message
        if "columns" in err.lower() or "Open" in err or "MultiIndex" in err:
            err = f"{symbol}: Data format error — try restarting the server (pip install --upgrade yfinance)"
        return jsonify({'error': err}), 404

    return jsonify({'error': f"{symbol}: No data returned — check the symbol (e.g. RELIANCE, TCS, NIFTY50)"}), 404


@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    conn = get_db()
    trades = conn.execute('SELECT id,trade_date,instrument,strategy_name,candle_type,candle_score,candle_note,possible_move,move_value,atr_direction,crsi_direction,crsi_level,market_structure,market_regime,htf_fvg,order_block,vix_direction,grade,trade_decision,option_type,strike_price,main_entry_price,main_cost,option_amount,hedge_type,hedge_strike_price,hedge_entry_price,hedge_cost,main_exit_price,main_exit_cost,hedge_exit_price,hedge_exit_cost,entry_reason,next_day_move,move_d2,move_d3,move_d4,move_d5,hedge_move_d1,hedge_move_d2,hedge_move_d3,hedge_move_d4,hedge_move_d5,result,pnl,pnl_amount,exit_reason,comments FROM trades ORDER BY trade_date DESC').fetchall()
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
