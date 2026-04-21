"""Classe base para workers. Padroniza logging, DB, tratamento de erro."""
from datetime import datetime
from src.db.connection import cursor
from src.utils.logger import get_logger


class BaseWorker:
    name = "base"

    def __init__(self):
        self.logger = get_logger(self.name)
        self.run_id = None
        self.items_processed = 0
        self.errors_count = 0

    def start_run(self):
        with cursor() as cur:
            cur.execute(
                "INSERT INTO run_logs (worker, status) VALUES (?, ?)",
                (self.name, "running"),
            )
            self.run_id = cur.lastrowid
        self.logger.info(f"Run {self.run_id} iniciado ({self.name})")

    def finish_run(self, status="success", log_text=""):
        with cursor() as cur:
            cur.execute(
                "UPDATE run_logs SET finished_at = CURRENT_TIMESTAMP, status = ?, "
                "items_processed = ?, errors_count = ?, log_text = ? WHERE id = ?",
                (status, self.items_processed, self.errors_count, log_text, self.run_id),
            )
        self.logger.info(
            f"Run {self.run_id} finalizado: {status} | {self.items_processed} itens | "
            f"{self.errors_count} erros"
        )

    def execute(self):
        """Implementado pelos workers concretos. Deve popular items_processed e errors_count."""
        raise NotImplementedError

    def run(self):
        self.start_run()
        log_lines = []
        try:
            self.execute()
            self.finish_run("success", "\n".join(log_lines))
        except Exception as e:
            self.errors_count += 1
            log_lines.append(f"FATAL: {e}")
            self.logger.exception("Erro fatal no worker")
            self.finish_run("failed", "\n".join(log_lines))
            raise
