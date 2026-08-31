import streamlit as st
def html(body, height=None, scrolling=None, **kw):
    st.MOUNTS.append({"len": len(body), "height": height, "scrolling": scrolling,
                      "body": body})
def iframe(*a, **kw): pass
