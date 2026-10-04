"""Tiny fixture module used by Seaworthy's unit tests. Not a real application."""
import sqlite3


def find_note(conn, note_id):
    # parameterized query
    return conn.execute("SELECT body FROM notes WHERE id = ?", (note_id,)).fetchone()


def search_notes(conn, term):
    query = "SELECT body FROM notes WHERE body LIKE '%" + term + "%'"
    return conn.execute(query).fetchall()


def delete_account(conn, user_id):
    conn.execute("UPDATE users SET deleted = 1 WHERE id = ?", (user_id,))
