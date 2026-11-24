"""Single process preserves in-process job coordination and SQLite ownership."""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
workers = 1
worker_class = "gthread"
threads = 4
timeout = 240
preload_app = False
