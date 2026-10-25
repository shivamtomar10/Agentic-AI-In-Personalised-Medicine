"""Browser end-to-end test (needs: pip install playwright; python -m playwright install chromium; server running on a fresh data/).
Set BASE_URL to change the address. Screenshots are written to ./e2e_shots."""
import os, pathlib
pathlib.Path("e2e_shots").mkdir(exist_ok=True)
import re, time
from playwright.sync_api import sync_playwright, expect
B=os.environ.get("BASE_URL","http://127.0.0.1:8000")
issues=[]
def watch(pg,name):
    pg.on("console",lambda m: issues.append((name,m.type,m.text)) if m.type in("error","warning") else None)
    pg.on("pageerror",lambda e: issues.append((name,"pageerror",str(e))))
with sync_playwright() as p:
    b=p.chromium.launch()
    # ---------- signed-out guards
    ctx0=b.new_context(); pg=ctx0.new_page(); watch(pg,"guard")
    pg.goto(B+"/portal"); assert "/login" in pg.url and "next=/portal" in pg.url, pg.url
    pg.goto(B+"/clinician"); assert "role=clinician" in pg.url, pg.url
    print("guards ok")
    # ---------- patient registers via the UI
    pc=b.new_context(viewport={"width":1280,"height":900}); pp=pc.new_page(); watch(pp,"patient")
    pp.goto(B+"/register")
    pp.fill("#display_name","Jordan Tester"); pp.fill("#email","jordan.%d@example.com" % __import__("time").time())
    pp.fill("#birth_date","1958-04-12"); pp.select_option("#gender","female")
    pp.click("button[type=submit]")                       # terms not accepted + no password -> field errors
    assert pp.locator(".field-error").count()>=1
    pp.fill("#password","short"); pp.check("#accept_terms"); pp.click("button[type=submit]")
    expect(pp.locator("#err-password")).to_be_visible()
    pp.fill("#password","lantern orchard velvet"); pp.click("button[type=submit]")
    pp.wait_for_url("**/portal"); pp.wait_for_selector("h1:has-text('Hello, Jordan')")
    pp.screenshot(path="e2e_shots/portal_empty.png")
    print("patient registered ->", pp.url)
    # ---------- check-in
    pp.click("text=Start your first check-in"); pp.wait_for_selector("#ci")
    pp.fill("#conditions","Type 2 diabetes mellitus (E11.9)\nHypertension (I10)"); pp.fill("#medications","Metformin 1000 mg BID\nLisinopril 20 mg QD\nIbuprofen 400 mg TID")
    pp.fill("#allergies","Penicillin (rash)")
    for k,v in dict(egfr=24,hba1c=9.4,potassium=5.8,creatinine=2.6,alt=30,sbp=148).items(): pp.fill("#"+k,str(v))
    pp.fill("#chief_complaint","routine follow-up")
    pp.screenshot(path="e2e_shots/portal_checkin.png",full_page=True)
    pp.click("button[type=submit]"); pp.wait_for_selector("text=Waiting for a clinician",timeout=15000)
    assert "#/cases/REC-" in pp.url, pp.url
    pp.wait_for_selector("text=Metformin is contraindicated")
    pp.screenshot(path="e2e_shots/portal_case_waiting.png",full_page=True)
    rec_id=pp.url.split("/cases/")[1]; print("case created", rec_id)
    # ---------- clinician signs in via the UI, in a SEPARATE browser context
    cc=b.new_context(viewport={"width":1280,"height":900}); cp=cc.new_page(); watch(cp,"clinician")
    cp.goto(B+"/login?role=clinician")
    cp.fill("#email","demo.clinician@example.com"); cp.fill("#password","wrong-password-here"); cp.click("button[type=submit]")
    cp.wait_for_selector("#form-msg .notice.bad:has-text('Incorrect email or password')")
    cp.click("text=Fill in these details"); cp.click("button[type=submit]")
    cp.wait_for_url("**/clinician"); cp.wait_for_selector("h1:has-text('Review queue')")
    cp.wait_for_selector(".qitem")
    cp.screenshot(path="e2e_shots/clin_queue.png")
    cp.click(f".qitem:has-text('{rec_id}')"); cp.wait_for_selector("text=Proposed actions for your decision")
    cp.wait_for_selector("text=Your decision")
    cp.screenshot(path="e2e_shots/clin_detail.png",full_page=True)
    # rationale required
    cp.click("button:has-text('Record decision')"); expect(cp.locator("#err-rationale, .field-error").first).to_be_visible()
    cp.fill("#rationale","Agree: stop metformin at eGFR 24 and review allergy conflict."); cp.click("button:has-text('Record decision')")
    cp.wait_for_selector("text=Approved by DR-DEMO",timeout=10000)
    print("clinician approved as DR-DEMO")
    # ---------- patient sees the decision without reloading (polling)
    pp.wait_for_selector("text=Reviewed by DR-DEMO",timeout=15000)
    pp.screenshot(path="e2e_shots/portal_case_decided.png",full_page=True)
    print("patient saw decision via polling")
    # ---------- patient overview + trends after second check-in
    pp.goto(B+"/portal#/check-in"); pp.wait_for_selector("#ci"); assert pp.input_value("#egfr")=="24", pp.input_value("#egfr")
    pp.fill("#egfr","23.5"); pp.click("button[type=submit]"); pp.wait_for_selector("text=Waiting for a clinician")
    pp.goto(B+"/portal#/"); pp.wait_for_selector(".trend"); pp.screenshot(path="e2e_shots/portal_home.png",full_page=True)
    # ---------- isolation: patient cannot open the clinician area; clinician cannot open the portal
    pp.goto(B+"/clinician"); pp.wait_for_url("**/portal"); print("patient bounced from /clinician")
    cp.goto(B+"/portal"); cp.wait_for_url("**/clinician"); print("clinician bounced from /portal")
    # ---------- sign out really ends the session
    pp.goto(B+"/portal"); pp.click("#account-slot >> text=Sign out"); pp.wait_for_url(B+"/")
    pp.goto(B+"/portal"); assert "/login" in pp.url; print("sign-out ok")
    # ---------- mobile screenshot
    mc=b.new_context(viewport={"width":390,"height":844}); mp=mc.new_page(); watch(mp,"mobile")
    mp.goto(B+"/login"); mp.click("text=Fill in these details"); mp.click("button[type=submit]"); mp.wait_for_url("**/portal"); mp.wait_for_selector(".trend"); mp.screenshot(path="e2e_shots/portal_mobile.png",full_page=True)
    mp.goto(B+"/"); mp.click("#menu-btn"); mp.screenshot(path="e2e_shots/home_menu_mobile.png")
    b.close()
print("ISSUES:",issues)
