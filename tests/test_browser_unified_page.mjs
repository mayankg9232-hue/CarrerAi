// Test Unified Single Page Pipeline in Real Headless Chrome
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const CHROME_PATH = process.platform === 'win32'
  ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
  : '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9224;
const TARGET_URL = 'http://127.0.0.1:8000/';

console.log('🚀 Launching Headless Chrome to test Unified Resume & Match Page...');

const userDir = path.join(os.tmpdir(), 'chrome_test_profile_' + Date.now());

const chrome = spawn(CHROME_PATH, [
  '--headless=new',
  `--remote-debugging-port=${PORT}`,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-gpu',
  `--user-data-dir=${userDir}`,
  TARGET_URL
]);

let ws = null;
let msgId = 1;
const pending = new Map();

async function initCDP() {
  for (let i = 0; i < 30; i++) {
    try {
      const resp = await fetch(`http://127.0.0.1:${PORT}/json`);
      if (resp.ok) {
        const targets = await resp.json();
        const pageTarget = targets.find(t => t.type === 'page');
        if (pageTarget && pageTarget.webSocketDebuggerUrl) {
          return pageTarget.webSocketDebuggerUrl;
        }
      }
    } catch (e) {}
    await sleep(250);
  }
  throw new Error('Failed to connect to Chrome debug port after 7.5s');
}

function sendCDP(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = msgId++;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evalInPage(script) {
  const res = await sendCDP('Runtime.evaluate', {
    expression: `(async () => { ${script} })()`,
    awaitPromise: true,
    returnByValue: true
  });
  if (res.result && res.result.exceptionDetails) {
    throw new Error('Script Exception: ' + JSON.stringify(res.result.exceptionDetails));
  }
  if (res.result && res.result.result) {
    return res.result.result.value;
  }
  return null;
}

async function run() {
  try {
    const wsUrl = await initCDP();
    console.log('✅ Connected to Headless Chrome CDP via WebSocket.');

    ws = new WebSocket(wsUrl);
    await new Promise((resolve) => ws.onopen = resolve);

    ws.onmessage = (evt) => {
      const data = JSON.parse(evt.data);
      if (data.method === 'Runtime.consoleAPICalled') {
        const text = (data.params.args || []).map(a => a.value !== undefined ? a.value : a.description).join(' ');
        console.log('    [Page Console]', text);
      }
      if (data.id && pending.has(data.id)) {
        const { resolve } = pending.get(data.id);
        pending.delete(data.id);
        resolve(data);
      }
    };

    await sendCDP('Runtime.enable');
    await sendCDP('Page.enable');
    await sleep(1500);

    // 1. Authenticate user into workspace
    console.log('\n--- Step 1: Sign in candidate to unlock workspace ---');
    await evalInPage(`document.getElementById('authTabSignUp').click();`);
    await sleep(300);
    await evalInPage(`
      const email = 'maya_' + Date.now() + '@example.com';
      document.getElementById('pageAuthFullName').value = 'Maya Chen';
      document.getElementById('pageAuthRole').value = 'candidate';
      document.getElementById('pageAuthEmail').value = email;
      document.getElementById('pageAuthPassword').value = 'CandidatePass123!';
      document.getElementById('btnPageAuthSubmit').click();
    `);

    // Wait for workspace reveal
    let authed = false;
    for (let i = 0; i < 30; i++) {
      const state = await evalInPage(`
        const card = document.getElementById('authPageContainer');
        return { isHidden: card && card.classList.contains('hidden'), hasToken: !!localStorage.getItem('cm_token') };
      `);
      if (state && state.isHidden && state.hasToken) {
        authed = true;
        break;
      }
      await sleep(350);
    }
    if (!authed) {
      throw new Error('Candidate failed to authenticate and unlock workspace!');
    }
    console.log('✅ Candidate signed in. Workspace unlocked.');

    // 2. Verify sidebar navigation: My resume, Job matches, Match & Explain MUST NOT BE VISIBLE
    console.log('\n--- Step 2: Verify Sidebar Navigation (My resume, Job matches, Match & Explain hidden) ---');
    const navVisibility = await evalInPage(`
      const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
      };
      const tabOverview = document.getElementById('tabOverview');
      const tabProfile = document.getElementById('tabProfile');
      const tabJobs = document.getElementById('tabJobs');
      const tabMatching = document.getElementById('tabMatching');

      return {
        tabOverviewText: tabOverview ? tabOverview.innerText.trim() : null,
        isOverviewVisible: isVisible(tabOverview),
        isProfileVisible: isVisible(tabProfile?.parentElement || tabProfile),
        isJobsVisible: isVisible(tabJobs?.parentElement || tabJobs),
        isMatchingVisible: isVisible(tabMatching?.parentElement || tabMatching)
      };
    `);

    console.log('  Overview Tab Text:', navVisibility.tabOverviewText);
    console.log('  Overview Visible:', navVisibility.isOverviewVisible);
    console.log('  My resume Visible:', navVisibility.isProfileVisible);
    console.log('  Job matches Visible:', navVisibility.isJobsVisible);
    console.log('  Match & Explain Visible:', navVisibility.isMatchingVisible);

    if (navVisibility.isProfileVisible || navVisibility.isJobsVisible || navVisibility.isMatchingVisible) {
      throw new Error('Navigation items that should be removed are still visible!');
    }
    console.log('✅ Step 2 PASSED: "My resume", "Job matches", and "Match & Explain" are completely hidden from user navigation.');

    // 3. Verify single page UI components
    console.log('\n--- Step 3: Verify Unified Single Page UI Structure ---');
    const pageUI = await evalInPage(`
      const dropzone = document.getElementById('dropzoneCv');
      const btnAiEngineer = document.getElementById('btnSampleAiEngineer');
      const btnFrontend = document.getElementById('btnSampleFrontend');
      const btnBackend = document.getElementById('btnSampleBackend');
      const progressCard = document.getElementById('pipelineProgressCard');
      const matchesList = document.getElementById('overviewMatchesList');

      return {
        hasDropzone: !!dropzone,
        hasSampleAi: !!btnAiEngineer,
        hasSampleFrontend: !!btnFrontend,
        hasSampleBackend: !!btnBackend,
        hasProgressCard: !!progressCard,
        hasMatchesList: !!matchesList
      };
    `);
    console.log('  Single Page UI Components:', pageUI);
    if (!pageUI.hasDropzone || !pageUI.hasSampleAi || !pageUI.hasMatchesList) {
      throw new Error('Single page UI is missing required elements!');
    }
    console.log('✅ Step 3 PASSED: Unified single page elements verified.');

    // Capture visual screenshot of the initial clean dropzone
    try {
      const shot0 = await sendCDP('Page.captureScreenshot', { format: 'png' });
      const data0 = shot0?.result?.data || shot0?.data;
      if (data0) {
        const out0 = 'C:\\\\Users\\\\mayan\\\\.gemini\\\\antigravity-ide\\\\brain\\\\ac692e36-3369-4c47-b11a-4ee891452a0d\\\\unified_hero_dropzone.png';
        fs.writeFileSync(out0, Buffer.from(data0, 'base64'));
        console.log(`📸 Initial Dropzone Screenshot saved to: ${out0}`);
      }
    } catch (e) {}

    // 4. Trigger Quick-Sample Resume Pipeline (Maya Chen)
    console.log('\n--- Step 4: Trigger Maya Chen Sample Resume Pipeline ---');
    await evalInPage(`
      document.getElementById('btnSampleAiEngineer').click();
    `);

    // 5. Observe Pipeline Stepper & Progress
    console.log('  Waiting for pipeline processing (Gemini-Embedding-2 + Database + Firecrawl + Matching Engine)...');
    let completed = false;
    for (let i = 0; i < 40; i++) {
      const pState = await evalInPage(`
        const summary = document.getElementById('candidateSummaryBanner');
        const isSummaryVisible = summary && summary.style.display !== 'none';
        const cards = document.querySelectorAll('#overviewMatchesList .ranked-job-card');
        const countBadge = document.getElementById('rankedMatchesCountBadge')?.innerText;
        return { isSummaryVisible, cardCount: cards.length, countBadge };
      `);
      if (pState && pState.isSummaryVisible && pState.cardCount > 0) {
        console.log('  Pipeline Result:', pState);
        completed = true;
        break;
      }
      await sleep(500);
    }

    if (!completed) {
      throw new Error('Pipeline did not complete within 20 seconds!');
    }

    // 6. Verify Extracted Candidate Profile & Ranked Job Matches
    console.log('\n--- Step 5: Verify Extracted Profile & Ranked Match Details ---');
    const matchDetails = await evalInPage(`
      const name = document.getElementById('summaryCandidateName')?.innerText;
      const headline = document.getElementById('summaryCandidateHeadline')?.innerText;
      const skills = Array.from(document.querySelectorAll('#summaryCandidateSkills .skill-match-tag')).map(el => el.innerText.trim());
      
      const cards = Array.from(document.querySelectorAll('#overviewMatchesList .ranked-job-card')).map(card => {
        const title = card.querySelector('h3')?.innerText;
        const score = card.querySelector('.match-percentage-pill')?.innerText;
        const matched = Array.from(card.querySelectorAll('.matched-skills-wrap .skill-match-tag')).map(el => el.innerText.trim());
        const quote = card.querySelector('.match-explanation-quote')?.innerText;
        return { title, score, matched, quote: quote ? quote.slice(0, 80) + '...' : '' };
      });

      return { name, headline, skills, cardsCount: cards.length, topCards: cards.slice(0, 3) };
    `);

    console.log('  Candidate Name:', matchDetails.name);
    console.log('  Candidate Headline:', matchDetails.headline);
    console.log('  Extracted Skills Count:', matchDetails.skills.length, matchDetails.skills.join(', '));
    console.log('  Total Ranked Job Cards Rendered:', matchDetails.cardsCount);
    console.log('  Top 3 Ranked Matches:');
    matchDetails.topCards.forEach((c, idx) => {
      console.log(`    #${idx + 1} ${c.title} — ${c.score}`);
      console.log(`       Matched: ${c.matched.join(', ')}`);
      console.log(`       Why: ${c.quote}`);
    });

    if (matchDetails.cardsCount === 0 || !matchDetails.topCards[0].score) {
      throw new Error('Ranked job matches were not rendered properly!');
    }

    // Capture visual screenshot of the finished page
    try {
      const shot = await sendCDP('Page.captureScreenshot', { format: 'png' });
      const data = shot?.result?.data || shot?.data;
      if (data) {
        const outPath = 'C:\\Users\\mayan\\.gemini\\antigravity-ide\\brain\\ac692e36-3369-4c47-b11a-4ee891452a0d\\unified_pipeline_page.png';
        fs.writeFileSync(outPath, Buffer.from(data, 'base64'));
        console.log(`📸 Visual Screenshot of Unified Match Page saved to: ${outPath}`);
      }
    } catch (e) {
      console.warn('Screenshot notice:', e.message);
    }

    console.log('\n🎉 ========================================================');
    console.log('🎉 ALL UNIFIED SINGLE PAGE PIPELINE VERIFICATIONS PASSED 100%!');
    console.log('🎉 ========================================================');

  } finally {
    if (ws) ws.close();
    chrome.kill('SIGTERM');
  }
}

run().catch(err => {
  console.error('❌ Test failed with error:', err);
  process.exit(1);
});
