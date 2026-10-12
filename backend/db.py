"""SQLite state for the HAUNT backend (stdlib sqlite3, WAL mode).

One connection guarded by a re-entrant lock; all mutations run inside an
explicit BEGIN IMMEDIATE transaction via DB.transaction(). The transaction
context manager is depth-aware so module functions can compose (e.g.
payments.verify_payment consuming a signature, granting an entitlement and
converting a slot hold atomically in one transaction).
"""
import contextlib
import sqlite3
import threading

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS quotes (
        request_id  TEXT PRIMARY KEY,
        wallet      TEXT,
        tier        TEXT NOT NULL,
        currency    TEXT NOT NULL,
        amount      REAL NOT NULL,
        memo        TEXT NOT NULL UNIQUE,
        hold_id     INTEGER,
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL,
        state       TEXT NOT NULL DEFAULT 'pending',
        source_ip   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS consumed_signatures (
        sig         TEXT PRIMARY KEY,
        request_id  TEXT NOT NULL,
        consumed_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS entitlements (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet      TEXT NOT NULL,
        tier        TEXT NOT NULL,
        granted_at  REAL NOT NULL,
        expires_at  REAL,
        source_sig  TEXT NOT NULL,
        note        TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS slot_holds (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet          TEXT,
        state           TEXT NOT NULL DEFAULT 'held',
        created_at      REAL NOT NULL,
        expires_at      REAL NOT NULL,
        pass_expires_at REAL,
        source_ip       TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS anomaly_quote_attempts (
        ip         TEXT NOT NULL,
        created_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS auth_challenges (
        nonce      TEXT PRIMARY KEY,
        wallet     TEXT NOT NULL,
        created_at REAL NOT NULL,
        used       INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ent_wallet ON entitlements(wallet)",
    "CREATE INDEX IF NOT EXISTS idx_holds_wallet ON slot_holds(wallet)",
]


class DB:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._depth = 0
        self.conn = sqlite3.connect(path, check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA synchronous=NORMAL')
            for stmt in SCHEMA:
                self.conn.execute(stmt)
            # All schema migrations run in ONE transaction: a crash or an
            # unrecognized table shape can never leave a half-rebuilt table
            # or a column half-added behind.
            with self.transaction():
                self._migrate_nullable_wallets()
                self._ensure_columns()

    def _migrate_nullable_wallets(self):
        """Relax quotes.wallet / slot_holds.wallet to nullable on databases
        created by earlier builds (they were NOT NULL). SQLite cannot alter
        a column constraint, so each affected table is rebuilt from its OWN
        stored CREATE TABLE statement with just the wallet column's NOT NULL
        removed — defaults, UNIQUE and AUTOINCREMENT survive verbatim, rows
        are copied 1:1, and slot_holds' index is recreated. No-op when the
        column is already nullable (or the table doesn't exist)."""
        import re
        for table, index_sql in (
                ('quotes', None),
                ('slot_holds',
                 'CREATE INDEX IF NOT EXISTS idx_holds_wallet'
                 ' ON slot_holds(wallet)')):
            info = self.conn.execute(
                'PRAGMA table_info(%s)' % table).fetchall()
            col = [r for r in info if r[1] == 'wallet']
            if not col or col[0][3] == 0:  # missing, or already nullable
                continue
            row = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table'"
                " AND name = ?", (table,)).fetchone()
            if row is None or not row[0]:
                continue
            new_sql, n = re.subn(r'wallet\s+TEXT\s+NOT\s+NULL',
                                 'wallet      TEXT', row[0], count=1)
            if n != 1:
                continue  # unrecognized shape — leave the table untouched
            new_sql = new_sql.replace(
                'CREATE TABLE %s' % table,
                'CREATE TABLE %s__mig' % table, 1)
            cols = ', '.join(r[1] for r in info)
            self.conn.execute(new_sql)
            self.conn.execute(
                'INSERT INTO %s__mig (%s) SELECT %s FROM %s'
                % (table, cols, cols, table))
            self.conn.execute('DROP TABLE %s' % table)
            self.conn.execute('ALTER TABLE %s__mig RENAME TO %s'
                              % (table, table))
            if index_sql:
                self.conn.execute(index_sql)

    # Columns added after the first schema shipped; _ensure_columns adds
    # any that are missing on databases created by earlier builds.
    ADDED_COLUMNS = (
        ('quotes', 'source_ip', 'TEXT'),
        ('slot_holds', 'source_ip', 'TEXT'),
        ('entitlements', 'note', 'TEXT'),
    )

    def _ensure_columns(self):
        for table, column, decl in self.ADDED_COLUMNS:
            info = self.conn.execute(
                'PRAGMA table_info(%s)' % table).fetchall()
            if not info:
                continue  # table itself is created by SCHEMA above
            if any(r[1] == column for r in info):
                continue
            self.conn.execute('ALTER TABLE %s ADD COLUMN %s %s'
                              % (table, column, decl))

    @contextlib.contextmanager
    def transaction(self):
        """Serialize writers; nested calls join the outer transaction."""
        with self._lock:
            if self._depth == 0:
                self.conn.execute('BEGIN IMMEDIATE')
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute('ROLLBACK')
                raise
            self._depth -= 1
            if self._depth == 0:
                self.conn.execute('COMMIT')

    def execute(self, sql, params=()):
        with self._lock:
            return self.conn.execute(sql, params)

    def query_one(self, sql, params=()):
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def query_all(self, sql, params=()):
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def close(self):
        with self._lock:
            self.conn.close()
