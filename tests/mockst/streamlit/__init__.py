"""
A recording stand-in for Streamlit.

Enough of the API to execute dashboard/app.py from top to bottom without
installing Streamlit or starting a browser, while recording what the script
asked for: every widget key, every block of raw HTML, and every canvas mount.

It records rather than renders, which is the point. The interesting failures in
a Streamlit host are structural - a duplicate widget key, a tab that never gets
built, a component mounted with the wrong height or with scrolling left on - and
all of those are visible in the call log. Widgets return their default value, so
the script always takes its first-paint path.

Used by tests/test_app_host.py. Not a general-purpose Streamlit replacement:
add to it only what app.py actually calls.
"""

LOG = []
KEYS = []          # every widget key used, to catch duplicates
HTML = []          # every unsafe_allow_html string
MOUNTS = []        # every components.html(...) call
STOPPED = []


class Stop(Exception):
    """What st.stop() raises, so a caller can tell a clean stop from a crash."""


class _SS(dict):
    def __getattr__(self, k): return self[k]

session_state = _SS()

class _Ctx:
    def __init__(self, name): self.name = name
    def __enter__(self): LOG.append(("enter", self.name)); return self
    def __exit__(self, *a): LOG.append(("exit", self.name)); return False
    # a column/container proxies the module-level API
    def __getattr__(self, item): return globals()[item]

class _Col(_Ctx):
    pass

def _key(k):
    if k is not None:
        KEYS.append(k)

def set_page_config(**kw): LOG.append(("page_config", kw))
def markdown(body, unsafe_allow_html=False, **kw):
    LOG.append(("markdown", len(body)))
    if unsafe_allow_html: HTML.append(body)
def caption(t, **kw): LOG.append(("caption", t))
def write(*a, **kw): LOG.append(("write", a))
def success(t, **kw): LOG.append(("success", t))
def error(t, **kw): LOG.append(("error", t))
def warning(t, **kw): LOG.append(("warning", t))
def info(t, **kw): LOG.append(("info", t))
def stop(): STOPPED.append(True); raise Stop()
def rerun(): LOG.append(("rerun", None))
def columns(spec, **kw):
    n = spec if isinstance(spec, int) else len(spec)
    LOG.append(("columns", spec))
    return [_Col(f"col{i}") for i in range(n)]
def tabs(labels):
    LOG.append(("tabs", tuple(labels)))
    return [_Ctx(l) for l in labels]
def container(**kw): return _Ctx("container")
def expander(label, **kw): return _Ctx("expander")
def spinner(text): return _Ctx("spinner")
def form(key, **kw): _key(key); return _Ctx("form")
def button(label, key=None, **kw): _key(key); LOG.append(("button", label)); return False
def form_submit_button(label, **kw): LOG.append(("submit", label)); return False
def text_input(label, key=None, value="", **kw): _key(key); return value
def selectbox(label, options, key=None, **kw):
    _key(key); options = list(options); return options[0] if options else None
def multiselect(label, options, key=None, default=None, **kw): _key(key); return list(default or [])
def date_input(label, value=None, key=None, **kw): _key(key); return value
def data_editor(data, key=None, **kw):
    _key(key); LOG.append(("data_editor", tuple(getattr(data, "columns", []) ), kw.get("height")))
    return data
def code(body, language=None, **kw): LOG.append(("code", len(body)))
def download_button(label, data=None, file_name=None, mime=None, key=None, **kw):
    _key(key); LOG.append(("download_button", label)); return False
def dataframe(data, key=None, **kw): _key(key); return data
def metric(*a, **kw): LOG.append(("metric", a))

sidebar = _Ctx("sidebar")

class _ColCfg:
    @staticmethod
    def TextColumn(label, **kw): return ("text", label, kw)
    @staticmethod
    def DateColumn(label, **kw): return ("date", label, kw)
    @staticmethod
    def NumberColumn(label, **kw): return ("num", label, kw)
column_config = _ColCfg()

class _CacheData:
    def __call__(self, *a, **kw):
        if a and callable(a[0]):
            return a[0]
        def deco(fn): return fn
        return deco
    def clear(self): LOG.append(("cache_clear", None))
cache_data = _CacheData()
cache_resource = _CacheData()
