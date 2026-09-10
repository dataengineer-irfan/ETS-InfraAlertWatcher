import sys

with open('dashboard/release_plan.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

out = []
for i, line in enumerate(lines):
    if "# --------------------------------------------------------------------------" in line and "404" in str(i):
        break
    if i == 403:
        break
    out.append(line)

new_content = """    # --------------------------------------------------------------------------
    # 6. EXECUTIVE RELEASE STORYBOARD (Previous, Current, Upcoming)
    # --------------------------------------------------------------------------
    st.markdown("<div style='margin-top:12px;margin-bottom:8px;font-size:12px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:0.04em;'>Enterprise Release Storyboard</div>", unsafe_allow_html=True)

    story_c1, story_c2, story_c3 = st.columns(3, gap="medium")

    def _render_story_card(title: str, rel_data: dict | None, accent_color: str, icon: str):
        if not rel_data:
            return f'''
            <div style="background:#181b1f;border:1px solid #2c3235;border-top:3px solid #2c3235;border-radius:2px;padding:12px;min-height:300px;display:flex;align-items:center;justify-content:center;">
              <div style="text-align:center;color:var(--mute);font-size:11px;">
                <div>{icon}</div>
                <div style="margin-top:6px;">No {title} Found</div>
              </div>
            </div>
            '''
            
        rid = rel_data.get("release_id", "Unknown")
        state_mmis = rel_data.get("state", "Unknown")
        pd_date = rel_data.get("prod_deploy_date", "TBD")
        readiness = rel_data.get("readiness_pct", 0)
        
        dev_f = rel_data.get("dev_end_date", "TBD")
        sit_f = rel_data.get("sit_end_date", "TBD")
        uat_f = rel_data.get("uat_end_date", "TBD")
        gn_d = rel_data.get("go_nogo_date", "TBD")
        
        return f'''
        <div style="background:#181b1f;border:1px solid #2c3235;border-top:3px solid {accent_color};border-radius:2px;padding:12px;min-height:280px;display:flex;flex-direction:column;justify-content:space-between;">
          <div>
            <div style="display:flex;align-items:center;gap:6px;font-size:10px;font-weight:700;color:var(--slate);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px;">
              <span>{icon}</span> {title}
            </div>
            <div style="font-size:18px;font-weight:800;color:var(--ink);font-family:var(--mono);line-height:1.2;">
              {rid}
            </div>
            <div style="font-size:11px;font-weight:600;color:{accent_color};margin-top:2px;">
              {state_mmis} MMIS Scope
            </div>
          </div>
          
          <div style="margin:16px 0;background:#141619;border:1px solid #2c3235;border-radius:2px;padding:8px;">
            <div style="font-size:9px;color:var(--mute);text-transform:uppercase;margin-bottom:6px;">Milestone Ledger</div>
            
            <div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:4px;border-bottom:1px dashed #2c3235;padding-bottom:2px;">
              <span style="color:var(--slate);">DEV Freeze</span>
              <span style="color:var(--text);font-family:var(--mono);">{dev_f}</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:4px;border-bottom:1px dashed #2c3235;padding-bottom:2px;">
              <span style="color:var(--slate);">SIT Exit</span>
              <span style="color:var(--text);font-family:var(--mono);">{sit_f}</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:4px;border-bottom:1px dashed #2c3235;padding-bottom:2px;">
              <span style="color:var(--slate);">UAT Sign-off</span>
              <span style="color:var(--text);font-family:var(--mono);">{uat_f}</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:4px;border-bottom:1px dashed #2c3235;padding-bottom:2px;">
              <span style="color:var(--slate);">Go / No-Go</span>
              <span style="color:var(--text);font-family:var(--mono);">{gn_d}</span>
            </div>
          </div>
          
          <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,0.02);border:1px solid #2c3235;padding:6px 10px;border-radius:2px;">
            <div>
              <div style="font-size:9px;color:var(--slate);text-transform:uppercase;">Cutover Target</div>
              <div style="font-size:12px;font-weight:700;color:var(--ink);font-family:var(--mono);">{pd_date}</div>
            </div>
            <div style="text-align:right;">
              <div style="font-size:9px;color:var(--slate);text-transform:uppercase;">Readiness</div>
              <div style="font-size:12px;font-weight:700;color:{accent_color};font-family:var(--mono);">{readiness:.0f}%</div>
            </div>
          </div>
        </div>
        '''

    with story_c1:
        prev_r = completed_releases[-1] if completed_releases else None
        st.markdown(_render_story_card("Previous Release", prev_r, "#73bf69", "📁"), unsafe_allow_html=True)
        
    with story_c2:
        curr_r = current_releases[0] if current_releases else None
        st.markdown(_render_story_card("Current Release", curr_r, "#38bdf8", "🎯"), unsafe_allow_html=True)
        
    with story_c3:
        next_r = upcoming_releases[0] if upcoming_releases else None
        st.markdown(_render_story_card("Upcoming Release", next_r, "#f59e0b", "🚀"), unsafe_allow_html=True)

    conn.close()
"""

out.append(new_content)

with open('dashboard/release_plan.py', 'w', encoding='utf-8') as f:
    f.writelines(out)
