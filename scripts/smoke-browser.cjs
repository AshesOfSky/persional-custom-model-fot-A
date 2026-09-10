const { chromium } = require('../apps/web/node_modules/playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport:{width:1600,height:1000}, deviceScaleFactor:1});
    const failures=[], responses=[];
    page.on('pageerror', e => failures.push(e.message));
    page.on('response', r => {if(r.url().includes('/core/')) responses.push({path:new URL(r.url()).pathname,status:r.status()});});
    await page.goto(process.env.TERMINAL_URL || 'http://127.0.0.1:7100/',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => document.querySelectorAll('canvas').length >= 6, {timeout:45000});
    await page.waitForResponse(r=>r.url().endsWith('/v1/analysis/research') && r.status()===200,{timeout:60000});
    await page.getByRole('tab',{name:'分析',exact:true}).click();
    await page.waitForFunction(() => !document.body.innerText.includes('正在计算共享分析'), {timeout:30000});
    for(const name of ['斐波那契','江恩','波浪','缠论']) await page.getByRole('button',{name,exact:true}).click();
    await page.waitForResponse(r=>r.url().endsWith('/v1/analysis/structures') && r.status()===200,{timeout:45000});
    if(await page.locator('[data-testid="worker-health-status"]').count()) throw Error('Monitoring entry is still visible');
    if(await page.getByRole('combobox',{name:/监控频率/}).count()) throw Error('Monitoring controls are still visible');
    const out=path.join(__dirname,'../artifacts');fs.mkdirSync(out,{recursive:true});
    await page.screenshot({path:path.join(out,'market-smoke.png')});
    const downloadPromise=page.waitForEvent('download',{timeout:90000});
    await page.getByRole('button',{name:/导出.*PDF/}).click();
    const download=await downloadPromise;
    const target=path.join(out,'market-smoke.pdf');await download.saveAs(target);
    if(fs.readFileSync(target).subarray(0,5).toString()!=='%PDF-')throw Error('Invalid PDF output');
    if(failures.length)throw Error(failures.join('; '));
    console.log(JSON.stringify({canvasCount:await page.locator('canvas').count(),pageErrors:failures,requests:responses,pdfBytes:fs.statSync(target).size}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1});
