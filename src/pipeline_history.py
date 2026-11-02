"""Record real pipeline stages without changing the existing file-based resume."""

import contextvars
import functools
import inspect
import logging

from src.storage import Store

_current = contextvars.ContextVar("studio_pipeline_run", default=None)


class _HistoryHandler(logging.Handler):
    def emit(self, record):
        active = _current.get()
        if not active:
            return
        message = record.getMessage()
        if record.levelno < logging.WARNING and not message.startswith(("STEP ", "PIPELINE COMPLETE")):
            return
        store, run_id, _language = active
        try:
            store.event(run_id, record.levelname, message)
        except Exception:
            # Logging must not destroy a successfully synthesized segment.
            self.handleError(record)


def bind_project(path, language):
    active = _current.get()
    if active:
        store, run_id, _ = active
        project_id = store.project(path, language, status="running")
        store.bind_run(run_id, project_id)


def track_pipeline(language):
    def decorate(function):
        signature = inspect.signature(function)
        @functools.wraps(function)
        def run(*args, **kwargs):
            import config
            store = Store()
            values = signature.bind(*args, **kwargs)
            values.apply_defaults()
            settings = {key: value for key, value in vars(config).items()}
            settings.update({"SELECTED_VOICE": values.arguments.get("voice") or values.arguments.get("voice_id"),
                             "BURN_SUBTITLES": values.arguments.get("burn_subtitles"),
                             "OCR_REPLACE": values.arguments.get("ocr_replace"),
                             "BG_MODE": values.arguments.get("bg_mode"),
                             "SOURCE_LANGUAGE": values.arguments.get("source_lang")})
            from src.subtitle_style import SubtitleStyle, load_project_subtitles
            if values.arguments.get("resume_dir"):
                settings.update(load_project_subtitles(values.arguments["resume_dir"], settings))
            colors = values.arguments.get("subtitle_style")
            if colors is not None:
                settings.update(colors.to_settings() if isinstance(colors, SubtitleStyle) else colors)
            if values.arguments.get("subtitle_font"):
                settings["SUBTITLE_FONT"] = values.arguments["subtitle_font"]
            if values.arguments.get("subtitle_size"):
                settings["SUBTITLE_FONT_SIZE"] = values.arguments["subtitle_size"]
            run_id = store.begin_run(language, settings)
            handler = _HistoryHandler()
            root = logging.getLogger()
            root.addHandler(handler)
            token = _current.set((store, run_id, language))
            path = values.arguments.get("resume_dir")
            try:
                if path:
                    bind_project(path, language)
                result = function(*args, **kwargs)
                path = result.get("work_dir") or result.get("output_dir") or path
                status = "pending" if result.get("status") == "translate_pending" else "done"
                if path:
                    project_id = store.sync_project(path, language, status=status, include_artifacts=True)
                    store.bind_run(run_id, project_id)
                store.finish_run(run_id, status)
                return result
            except Exception as error:
                store.finish_run(run_id, "failed", error)
                raise
            finally:
                _current.reset(token)
                root.removeHandler(handler)
                handler.close()
        return run
    return decorate
