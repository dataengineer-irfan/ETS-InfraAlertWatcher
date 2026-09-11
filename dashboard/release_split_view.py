import streamlit as st
from datetime import datetime
import pandas as pd

from db import get_connection, get_release_schedules
from dashboard.release_plan import _get_stage_info, _days_between, render_html

def _render_dense_story_card(title: str, rel_data: dict | None, accent_color: str, icon: str):
    if not rel_data:
        return f'''
        <div style="background:#181b1f;border:1px solid #2c3235;border-left:3px solid #2c3235;border-radius:2px;padding:8px 12px;margin-bottom:8px;display:flex;align-items:center;justify-content:center;min-height:90px;">
            <div style="text-align:center;color:var(--mute);font-size:11px;">
                <div>{icon}</div>
                <div style="margin-top:4px;">No {title} Found</div>
            </div>
        </div>
        '''

    rid = rel_data.get("release_id", "Unknown")
    state_mmis = rel_data.get("state", "Unknown")
    target_env = "ENV05" if state_mmis == "NH" else ("PRM" if state_mmis == "ND" else "ENV 30")
    
    dev_f = rel_data.get("dev_end_date", "TBD")
    sit_f = rel_data.get("sit_end_date", "TBD")
    uat_f = rel_data.get("uat_end_date", "TBD")
    p_d = rel_data.get("prod_deploy_date", "TBD")
    readiness = rel_data.get("readiness_pct", 0)
    
    # Compact vertical layout for left column
    return f'''
    <div style="background:#181b1f;border:1px solid #2c3235;border-left:3px solid {accent_color};border-radius:2px;padding:12px;margin-bottom:8px;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
                <div style="display:flex;align-items:center;gap:6px;font-size:9.5px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px;">
                    <span>{icon}</span> {title}
                </div>
                <div style="font-size:15px;font-weight:800;color:var(--text);letter-spacing:0.02em;">
                    {state_mmis}.{rid}
                </div>
                <div style="font-size:10px;font-weight:600;color:{accent_color};margin-top:2px;">
                    {state_mmis} Target: {target_env}
                </div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:9px;color:var(--mute);text-transform:uppercase;">Readiness</div>
                <div style="font-size:16px;font-weight:700;color:#38bdf8;">{readiness}%</div>
            </div>
        </div>
        <div style="margin-top:12px;border-top:1px solid #2c3235;padding-top:8px;">
            <table style="width:100%;font-size:10px;color:var(--text);border-collapse:collapse;">
                <tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:3px 0;color:var(--slate);">DEV Freeze</td><td style="text-align:right;font-family:var(--mono);">{dev_f}</td></tr>
                <tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:3px 0;color:var(--slate);">SIT Exit</td><td style="text-align:right;font-family:var(--mono);">{sit_f}</td></tr>
                <tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:3px 0;color:var(--slate);">UAT Sign-off</td><td style="text-align:right;font-family:var(--mono);">{uat_f}</td></tr>
                <tr><td style="padding:3px 0;color:var(--slate);">Cutover Target</td><td style="text-align:right;font-family:var(--mono);color:var(--text);font-weight:700;">{p_d}</td></tr>
            </table>
        </div>
    </div>
    '''

def render_release_split_view(db_path: str):
    """Render Option 2: Side-by-Side Split Pane layout for Executive Release Tracking."""
    conn = get_connection(db_path)
    now_iso = datetime.now().strftime("%Y-%m-%d")
    
    # RBAC logic (re-used from release plan)
    active_user = st.session_state.get("active_user", "admin")
    user_assigned_state = st.session_state.get("assigned_state")
    if not user_assigned_state:
        u_low = active_user.lower()
        if "ak" in u_low: user_assigned_state = "AK"
        elif "nd" in u_low: user_assigned_state = "ND"
        elif "nh" in u_low: user_assigned_state = "NH"
    
    state_filter = None if user_assigned_state == "Enterprise" else user_assigned_state
    
    # Header
    st.markdown("""
        <div style="margin-bottom:16px;">
            <div style="font-size:18px;font-weight:800;letter-spacing:-.02em;color:var(--text);">EXECUTIVE SPLIT RADAR</div>
            <div style="font-size:11px;color:var(--slate);">High-Density Release Tracker & Matrix (Zero-Scroll Interface)</div>
        </div>
    """, unsafe_allow_html=True)
    
    # Optional Top Filter
    f_c1, f_c2 = st.columns([4.0, 1.0])
    with f_c2:
        if user_assigned_state == "Enterprise":
            disp_state = st.selectbox("Scope", ["All", "NH", "ND", "AK"], key="split_state_filter", label_visibility="collapsed")
            if disp_state != "All":
                state_filter = disp_state
        else:
            st.markdown(f"<div style='text-align:right;font-size:12px;font-weight:700;color:var(--accent);padding-top:8px;'>{user_assigned_state} Scope Lock</div>", unsafe_allow_html=True)
    
    all_releases = get_release_schedules(conn, state=state_filter)
    conn.close()
    
    all_releases.sort(key=lambda x: x.get("prod_deploy_date", "9999-12-31"))
    
    prev_r, cur_r, next_r = None, None, None
    future_rels = [r for r in all_releases if r.get("prod_deploy_date", "") >= now_iso]
    past_rels = [r for r in all_releases if r.get("prod_deploy_date", "") < now_iso]
    
    if future_rels:
        cur_r = future_rels[0]
        if len(future_rels) > 1:
            next_r = future_rels[1]
    if past_rels:
        prev_r = past_rels[-1]
    
    # Split Layout (Option 2)
    col_left, col_right = st.columns([0.45, 0.55], gap="large")
    
    with col_left:
        st.markdown("<div style='font-size:10px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;'>Release Sequence Profile</div>", unsafe_allow_html=True)
        render_html(_render_dense_story_card("Previous Release", prev_r, "#73bf69", "🟢"))
        render_html(_render_dense_story_card("Active Release", cur_r, "#38bdf8", "🔵"))
        render_html(_render_dense_story_card("Upcoming Release", next_r, "#f59e0b", "🟠"))
        
    with col_right:
        st.markdown("<div style='font-size:10px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;'>Granular Pipeline Matrix</div>", unsafe_allow_html=True)
        
        # Interactive Release Picker (so the right pane is dynamic)
        release_dict = {r["release_id"]: r for r in all_releases}
        all_rel_ids = list(release_dict.keys())
        
        if not all_rel_ids:
            st.info("No releases found for this scope.")
        else:
            chosen_rel = st.selectbox("Inspect Matrix", all_rel_ids, key="split_matrix_picker", label_visibility="collapsed")
            rel_data = release_dict.get(chosen_rel)
            
            if rel_data:
                # Right Panel Matrix Details
                d_end = rel_data.get('prod_deploy_date', 'TBD')
                is_deployed = d_end < now_iso
                status_badge = '<span style="color:#73bf69;background:rgba(115,191,105,0.1);padding:2px 6px;border-radius:2px;">DEPLOYED</span>' if is_deployed else '<span style="color:#38bdf8;background:rgba(56,189,248,0.1);padding:2px 6px;border-radius:2px;">ACTIVE</span>'
                
                st.markdown(f'''
                <div style="background:#141619;border:1px solid #2c3235;border-radius:2px;padding:12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-size:13px;font-weight:800;color:var(--text);">{rel_data['state']}.{rel_data['release_id']} Command Deck</div>
                        <div style="font-size:10px;color:var(--slate);">RM: {rel_data.get('state_rm_name', 'Unassigned')}</div>
                    </div>
                    <div style="font-size:10px;font-weight:700;">{status_badge}</div>
                </div>
                ''', unsafe_allow_html=True)
                
                st.markdown('''
                <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;padding:12px;margin-bottom:8px;">
                    <table class="tblx" style="width:100%;font-size:10px;">
                        <tr style="background:#141619;"><th style="text-align:left;">Phase Gate</th><th style="text-align:left;">Target Environment</th><th style="text-align:left;">Date</th><th style="text-align:right;">Gate Status</th></tr>
                        <tr><td>DEV Freeze</td><td>Build-76 / ENV52</td><td style="font-family:var(--mono);">{dev}</td><td style="text-align:right;">{d_stat}</td></tr>
                        <tr><td>SIT QA Gate</td><td>SIT QA / ENV57</td><td style="font-family:var(--mono);">{sit}</td><td style="text-align:right;">{s_stat}</td></tr>
                        <tr><td>UAT Acceptance</td><td>Acceptance / ENV04</td><td style="font-family:var(--mono);">{uat}</td><td style="text-align:right;">{u_stat}</td></tr>
                        <tr><td>Go / No-Go</td><td>Decision Board</td><td style="font-family:var(--mono);">{gn}</td><td style="text-align:right;">{gn_stat}</td></tr>
                        <tr><td>PROD Cutover</td><td>PROD / ENV05</td><td style="font-family:var(--mono);font-weight:700;color:var(--text);">{prod}</td><td style="text-align:right;">{p_stat}</td></tr>
                    </table>
                </div>
                '''.format(
                    dev=rel_data.get('dev_end_date', 'TBD'), d_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(rel_data.get('dev_end_date', '')) < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>',
                    sit=rel_data.get('sit_end_date', 'TBD'), s_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(rel_data.get('sit_end_date', '')) < now_iso else '<span style="color:#ff9830;font-weight:700;">IN PROGRESS</span>',
                    uat=rel_data.get('uat_end_date', 'TBD'), u_stat='<span style="color:#73bf69;font-weight:700;">PASSED</span>' if str(rel_data.get('uat_end_date', '')) < now_iso else '<span style="color:#5794f2;font-weight:700;">PENDING</span>',
                    gn=rel_data.get('go_nogo_date', 'TBD'), gn_stat='<span style="color:#73bf69;font-weight:700;">GO</span>' if str(rel_data.get('go_nogo_date', '')) < now_iso else '<span style="color:#5794f2;font-weight:700;">AWAITING</span>',
                    prod=rel_data.get('prod_deploy_date', 'TBD'), p_stat='<span style="color:#73bf69;font-weight:700;">LIVE</span>' if str(rel_data.get('prod_deploy_date', '')) < now_iso else '<span style="color:#5794f2;font-weight:700;">SCHEDULED</span>'
                ), unsafe_allow_html=True)
                
            # Full roadmap below the active selection
            roadmap_rows = []
            for r in all_releases:
                d_end = r.get('prod_deploy_date', 'TBD')
                status = '<span style="color:#73bf69;font-weight:700;">DEPLOYED</span>' if d_end < now_iso else '<span style="color:#ff9830;font-weight:700;">SCHED</span>'
                roadmap_rows.append(f"<tr><td>{r.get('state')}</td><td><b>{r.get('release_id')}</b></td><td style='font-family:var(--mono);'>{r.get('dev_end_date', 'TBD')}</td><td style='font-family:var(--mono);'>{r.get('uat_end_date', 'TBD')}</td><td style='font-family:var(--mono);'>{d_end}</td><td style='text-align:right;'>{status}</td></tr>")
                
            st.markdown(f'''
            <div style="background:#181b1f;border:1px solid #2c3235;border-radius:2px;max-height:190px;overflow-y:auto;">
                <table class="tblx" style="width:100%;font-size:10px;">
                    <tr style="position:sticky;top:0;background:#141619;border-bottom:1px solid #2c3235;z-index:2;">
                        <th style="text-align:left;">St</th><th style="text-align:left;">Rel</th><th style="text-align:left;">DEV</th><th style="text-align:left;">UAT</th><th style="text-align:left;">PROD</th><th style="text-align:right;">Stat</th>
                    </tr>
                    {''.join(roadmap_rows)}
                </table>
            </div>
            ''', unsafe_allow_html=True)
