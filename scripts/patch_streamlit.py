"""
scripts/patch_streamlit.py
==========================
Injects the Expiry Watchtower keyboard sanitizer, WebSocket auto-reconnect watchdog,
and enterprise error suppressor directly into Streamlit's static index.html.

This eliminates the need for bridge iframes (components.html), completely removing:
  1. react-dom Unrecognized feature: 'wake-lock'
  2. about:srcdoc:1 An iframe which has both allow-scripts and allow-same-origin
  3. Streamlit toLowerCase reconnect crash on undefined event.key
"""

import pathlib
import sys

PATCH_MARKER = "/* ETS_WATCHDOG_PATCH */"

PATCH_SCRIPT = """
    <!-- ETS Watchtower Resilience Watchdog & Hotkey Sanitizer -->
    <script>
    (function() {
      /* ETS_WATCHDOG_PATCH */
      if (window.__ets_patch_applied) return;
      window.__ets_patch_applied = true;

      // 1. Console filter for deprecated browser features and benign iframe warnings
      try {
        const _warn = console.warn;
        const _err = console.error;
        const isSuppressed = function(m) {
          if (typeof m !== 'string') return false;
          return m.indexOf('Unrecognized feature:') !== -1 ||
                 m.indexOf('wake-lock') !== -1 ||
                 m.indexOf('ambient-light-sensor') !== -1 ||
                 m.indexOf('escape its sandboxing') !== -1 ||
                 m.indexOf('legacy-image-formats') !== -1 ||
                 m.indexOf('oversized-images') !== -1 ||
                 m.indexOf('Download Button source error') !== -1 ||
                 m.indexOf('source error - 404') !== -1 ||
                 m.indexOf('/media/') !== -1;
        };
        console.warn = function(...args) {
          if (args.length > 0 && isSuppressed(args[0])) return;
          return _warn.apply(console, args);
        };
        console.error = function(...args) {
          if (args.length > 0 && isSuppressed(args[0])) return;
          return _err.apply(console, args);
        };
      } catch (_) {}

      // 2. Protect against Streamlit core JS bug (reading 'toLowerCase' on undefined event.key)
      try {
        const proto = KeyboardEvent.prototype;
        const origKeyDesc = Object.getOwnPropertyDescriptor(proto, 'key');
        if (origKeyDesc && origKeyDesc.get) {
          const origKeyGet = origKeyDesc.get;
          Object.defineProperty(proto, 'key', {
            get: function() {
              try {
                const v = origKeyGet.call(this);
                return (typeof v === 'string') ? v : '';
              } catch (_) {
                return '';
              }
            },
            configurable: true,
            enumerable: true
          });
        }
      } catch (_) {}

      function sanitizeKeyEvent(e) {
        if (!e) return;
        if (typeof e.key === 'undefined' || e.key === null) {
          try {
            Object.defineProperty(e, 'key', {
              value: '',
              writable: true,
              configurable: true,
              enumerable: true
            });
          } catch (_) {}
        }
      }
      window.addEventListener('keydown', sanitizeKeyEvent, true);
      document.addEventListener('keydown', sanitizeKeyEvent, true);
      window.addEventListener('keyup', sanitizeKeyEvent, true);
      document.addEventListener('keyup', sanitizeKeyEvent, true);
      window.addEventListener('keypress', sanitizeKeyEvent, true);
      document.addEventListener('keypress', sanitizeKeyEvent, true);

      // Global uncaught error suppressor for toLowerCase
      window.addEventListener('error', function(evt) {
        if (evt && evt.message && evt.message.indexOf("reading 'toLowerCase'") !== -1) {
          evt.preventDefault();
          evt.stopImmediatePropagation();
          return true;
        }
      }, true);

      // 3. WebSocket Resilience & Auto-Reconnection Watchdog
      (function initWebSocketWatchdog() {
        let isReconnecting = false;
        let reconnectBanner = null;

        function getOrCreateBanner() {
          if (reconnectBanner && document.body && document.body.contains(reconnectBanner)) return reconnectBanner;
          if (!document.body) return null;
          reconnectBanner = document.createElement('div');
          reconnectBanner.id = 'ets-reconnect-watchdog-banner';
          reconnectBanner.style.cssText = [
            'position: fixed',
            'bottom: 18px',
            'right: 18px',
            'z-index: 9999999',
            'background: rgba(15, 23, 42, 0.95)',
            'border: 1px solid #0284C7',
            'box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6), 0 0 15px rgba(56, 189, 248, 0.25)',
            'border-radius: 8px',
            'padding: 10px 16px',
            'display: flex',
            'align-items: center',
            'gap: 10px',
            'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            'font-size: 12px',
            'font-weight: 600',
            'color: #F8FAFC',
            'backdrop-filter: blur(8px)',
            'transition: opacity 0.3s ease, transform 0.3s ease',
            'transform: translateY(100px)',
            'opacity: 0',
            'pointer-events: none'
          ].join(';');
          reconnectBanner.innerHTML = '<span id="ets-reconnect-dot" style="width:9px; height:9px; border-radius:50%; background:#F59E0B; display:inline-block; box-shadow:0 0 8px #F59E0B;"></span><span id="ets-reconnect-text">Reconnecting to Expiry Watchtower...</span>';

          const style = document.createElement('style');
          style.textContent = '@keyframes etsPulse { 0%, 100% { transform: scale(0.9); opacity: 0.6; } 50% { transform: scale(1.2); opacity: 1; } } #ets-reconnect-dot { animation: etsPulse 1.2s infinite ease-in-out; }';
          document.head.appendChild(style);
          document.body.appendChild(reconnectBanner);
          return reconnectBanner;
        }

        function showBanner(text, isOk) {
          const b = getOrCreateBanner();
          if (!b) return;
          const txtSpan = b.querySelector('#ets-reconnect-text');
          const dot = b.querySelector('#ets-reconnect-dot');
          if (txtSpan) txtSpan.textContent = text;
          if (dot) {
            dot.style.background = isOk ? '#10B981' : '#F59E0B';
            dot.style.boxShadow = isOk ? '0 0 8px #10B981' : '0 0 8px #F59E0B';
          }
          b.style.transform = 'translateY(0)';
          b.style.opacity = '1';
        }

        function hideBanner() {
          if (reconnectBanner) {
            reconnectBanner.style.transform = 'translateY(100px)';
            reconnectBanner.style.opacity = '0';
          }
        }

        function triggerAutoReconnect() {
          if (isReconnecting) return;
          isReconnecting = true;
          showBanner('Connection interrupted. Restoring session...', false);

          let attempts = 0;
          const pollHealth = function() {
            attempts++;
            fetch('/_stcore/health?t=' + Date.now(), { cache: 'no-store' })
              .then(function(res) {
                if (res.ok) {
                  showBanner('Server reachable! Reloading...', true);
                  setTimeout(function() {
                    window.location.reload();
                  }, 400);
                } else {
                  showBanner('Server waking up (attempt ' + attempts + ')...', false);
                  setTimeout(pollHealth, 2500);
                }
              })
              .catch(function() {
                showBanner('Reconnecting to server (attempt ' + attempts + ')...', false);
                setTimeout(pollHealth, 2500);
              });
          };

          setTimeout(pollHealth, 1200);
        }

        // Intercept parent WebSocket creation
        if (typeof window.WebSocket !== 'undefined') {
          const OrigWS = window.WebSocket;
          window.WebSocket = function(url, protocols) {
            const ws = (typeof protocols !== 'undefined') ? new OrigWS(url, protocols) : new OrigWS(url);
            window.__ets_active_ws = ws;

            ws.addEventListener('open', function() {
              isReconnecting = false;
              hideBanner();
            });

            ws.addEventListener('close', function() {
              triggerAutoReconnect();
            });

            ws.addEventListener('error', function() {
              triggerAutoReconnect();
            });

            return ws;
          };
          window.WebSocket.prototype = OrigWS.prototype;
          window.WebSocket.CONNECTING = OrigWS.CONNECTING;
          window.WebSocket.OPEN = OrigWS.OPEN;
          window.WebSocket.CLOSING = OrigWS.CLOSING;
          window.WebSocket.CLOSED = OrigWS.CLOSED;
          for (const key of Object.getOwnPropertyNames(OrigWS)) {
            try {
              if (typeof window.WebSocket[key] === 'undefined') {
                window.WebSocket[key] = OrigWS[key];
              }
            } catch (_) {}
          }
        }

        // Reconnect watchdog on tab wake-up / visibility change
        document.addEventListener('visibilitychange', function() {
          if (document.visibilityState === 'visible') {
            const ws = window.__ets_active_ws;
            if (ws && (ws.readyState === 2 || ws.readyState === 3)) {
              triggerAutoReconnect();
            }
          }
        });

        // Watch for Streamlit's connection error modal in DOM
        const observer = new MutationObserver(function() {
          const errEl = document.querySelector('[data-testid="stConnectionStatus"]');
          if (errEl) {
            triggerAutoReconnect();
          }
        });
        const targetNode = document.body || document.documentElement;
        if (targetNode) {
          observer.observe(targetNode, { childList: true, subtree: true });
        }
      })();

      // 4. Native Sidebar Rail Toggle & Navigation Engine
      function setupNav() {
        function getSidebar() { return document.querySelector('[data-testid="stSidebar"]'); }

        document.addEventListener('click', function(e) {
          const toggleBtn = e.target ? e.target.closest('#ets-rail-toggle-btn') : null;
          if (toggleBtn) {
            e.preventDefault();
            e.stopPropagation();
            const sb = getSidebar();
            if (sb) {
              const cur = sb.getAttribute('data-rail-state') || 'collapsed';
              sb.setAttribute('data-rail-state', cur === 'expanded' ? 'collapsed' : 'expanded');
            }
            return;
          }

          const closeBtn = e.target ? e.target.closest('#ets-close-panel-btn') : null;
          if (closeBtn) {
            e.preventDefault();
            e.stopPropagation();
            const sb = getSidebar();
            if (sb) sb.setAttribute('data-rail-state', 'collapsed');
            return;
          }

          const navBtn = e.target ? e.target.closest('.ets-nav-item') : null;
          if (navBtn) {
            e.preventDefault();
            e.stopPropagation();
            const idx = parseInt(navBtn.getAttribute('data-nav-idx'), 10);
            if (!isNaN(idx)) {
              const topTabs = document.querySelectorAll('[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stTabs"] [role="tab"]');
              if (topTabs && topTabs[idx]) {
                topTabs[idx].click();
              } else {
                const topTabsContainer = document.querySelector('[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > [data-testid="stTabs"]');
                const tl = topTabsContainer ? topTabsContainer.querySelector('[role="tablist"]') : null;
                if (tl && tl.children[idx]) {
                  tl.children[idx].click();
                }
              }
              document.querySelectorAll('.ets-nav-item').forEach(function(b) { b.classList.remove('active'); });
              navBtn.classList.add('active');
              const sb = getSidebar();
              if (sb) setTimeout(function() { sb.setAttribute('data-rail-state', 'collapsed'); }, 120);
            }
            return;
          }
        }, true);
      }

      // 5. Automatic Deep-Link & URL Query Parameter Tab Synchronizer
      function syncTabFromUrl() {
        try {
          const p = new URLSearchParams(window.location.search);
          let targetTabIdx = null;
          if (p.has('tab')) {
            targetTabIdx = parseInt(p.get('tab'), 10);
          } else if (p.has('op_kpi') || p.has('op_cell') || p.has('op_act_id')) {
            targetTabIdx = 2; // Operations Hub
          }
          if (targetTabIdx !== null && !isNaN(targetTabIdx)) {
            let tries = 0;
            const tabTimer = setInterval(function() {
              tries++;
              const tabs = document.querySelectorAll('[data-testid="stTabs"] [role="tab"]');
              if (tabs && tabs.length > targetTabIdx) {
                const targetTab = tabs[targetTabIdx];
                if (targetTab && targetTab.getAttribute('aria-selected') !== 'true') {
                  targetTab.click();
                }
                clearInterval(tabTimer);
              } else if (tries > 80) {
                clearInterval(tabTimer);
              }
            }, 50);
          }
        } catch (_) {}
      }

      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
          setupNav();
          syncTabFromUrl();
        });
      } else {
        setupNav();
        syncTabFromUrl();
      }
    })();
    </script>
"""


def patch_iframe_util() -> bool:
    try:
        import streamlit
        sp = pathlib.Path(streamlit.__file__).parent / "static" / "static" / "js"
        if not sp.exists():
            return False
        clean_content = "var e=void 0,t=void 0;export{e as n,t};"
        count = 0
        for f in sp.glob("IFrameUtil*.js"):
            txt = f.read_text(encoding="utf-8")
            if txt != clean_content:
                f.write_text(clean_content, encoding="utf-8")
                count += 1
        if count > 0:
            print(f"[+] Cleaned deprecated iframe feature and sandbox policies from {count} files.")
        return True
    except Exception as e:
        print(f"[!] Error patching IFrameUtil: {e}", file=sys.stderr)
        return False


def patch_index_html() -> bool:
    try:
        patch_iframe_util()
        import streamlit
        idx_path = pathlib.Path(streamlit.__file__).parent / "static" / "index.html"
        if not idx_path.exists():
            print(f"[!] Streamlit static index.html not found at: {idx_path}")
            return False

        content = idx_path.read_text(encoding="utf-8")
        if PATCH_MARKER in content:
            if "syncTabFromUrl" in content:
                print("[+] Streamlit static index.html is already patched with syncTabFromUrl.")
                return True
            import re
            cleaned = re.sub(r'<!-- ETS Watchtower Resilience Watchdog & Hotkey Sanitizer -->[\s\S]*?</script>', '', content)
            new_content = cleaned.replace("</head>", f"{PATCH_SCRIPT}\n  </head>")
            idx_path.write_text(new_content, encoding="utf-8")
            print(f"[+] Successfully upgraded patch on {idx_path}")
            return True

        if "</head>" not in content:
            print("[!] Could not find </head> tag in index.html")
            return False

        new_content = content.replace("</head>", f"{PATCH_SCRIPT}\n  </head>")
        idx_path.write_text(new_content, encoding="utf-8")
        print(f"[+] Successfully patched {idx_path}")
        return True
    except Exception as e:
        print(f"[!] Error patching index.html: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    s1 = patch_index_html()
    s2 = patch_iframe_util()
    sys.exit(0 if s1 else 1)

