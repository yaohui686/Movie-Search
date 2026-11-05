"""本地 Web 界面：浏览器里搜片、看直链、点下载、直接播放。

和命令行同一套内核，界面全在这一个文件里（纯标准库，无前端依赖）：

  GET  /                单页界面（搜索框 + 结果卡片）
  GET  /api/search?wd=  搜索（NDJSON 流式：进度行…最后一行结果）
  GET  /api/info?u=     查真实分辨率
  GET  /file?u=&name=   代理下载（防盗链 + 另存为 + 断点续传）
  GET  /play?u=         播放页（hls.js）
  GET  /proxy?u=        m3u8 防盗链代理
  GET  /dl?u=           ffmpeg 命令文本

由 run.py 的 web 模式调用：
  python run.py web
"""
from __future__ import annotations

import json
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import downloader, player, webdl

INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>影视检索 · 下载台</title>
<style>
  :root{
    --bg:#0b0b0f; --card:#14141c; --line:#262633; --txt:#e6e6e6;
    --dim:#8b8f98; --acc:#2d5af0; --ok:#2fa96a; --warn:#d0a03a;
  }
  html,body{margin:0;background:var(--bg);color:var(--txt);
    font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}
  #wrap{max-width:1000px;margin:0 auto;padding:20px 16px 60px}
  h1{font-size:20px;margin:4px 0 2px}
  h1 small{color:var(--dim);font-size:12px;font-weight:normal}
  .searchbar{display:flex;gap:10px;margin:16px 0 8px}
  .searchbar input{flex:1;background:#101018;border:1px solid var(--line);
    border-radius:8px;padding:11px 14px;color:var(--txt);font-size:15px;outline:none}
  .searchbar input:focus{border-color:var(--acc)}
  .searchbar button{background:var(--acc);color:#fff;border:0;border-radius:8px;
    padding:11px 22px;font-size:14px;cursor:pointer}
  .searchbar button:hover{filter:brightness(1.15)}
  .searchbar button:disabled{filter:grayscale(.6);cursor:wait}
  #status{min-height:22px;color:var(--dim);font-size:13px;margin:4px 0 12px;white-space:pre-wrap}
  #status .bar{color:var(--acc)}
  .statline{color:var(--dim);font-size:12px;margin:2px 0 14px}
  .topbtns{display:flex;gap:10px;margin:0 0 14px;flex-wrap:wrap}
  .topbtns button,.card .ops button,a.dl,button.cp{
    background:#1e1e26;color:#cfd3dc;border:1px solid #2c2c38;border-radius:6px;
    padding:6px 13px;cursor:pointer;font-size:13px;text-decoration:none}
  .topbtns button:hover,.card .ops button:hover,button.cp:hover{background:#2a2a36}
  .card .ops button.hot{background:var(--acc);border-color:var(--acc);color:#fff}
  .card .ops button.hot:hover{filter:brightness(1.15)}
  a.dl{background:var(--acc);border-color:var(--acc);color:#fff}
  a.dl:hover{filter:brightness(1.15)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
    padding:14px 16px;margin-bottom:12px}
  .card h2{font-size:15px;margin:0 0 2px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .tag{font-size:11px;border-radius:4px;padding:1px 7px;white-space:nowrap}
  .tag.q{background:#17321f;color:#5ad08a}
  .tag.q2{background:#1d3140;color:#6fc3df}
  .tag.dl{background:#152642;color:#7aa7ff}
  .tag.no{background:#33261526;color:var(--warn)}
  .tag.src{background:#1e1e26;color:var(--dim)}
  .meta{color:var(--dim);font-size:12px;margin:2px 0 10px;word-break:break-all}
  .ops{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
  .links{margin-top:6px;border-top:1px solid #1e1e2a;padding-top:4px}
  .row{display:flex;align-items:center;gap:10px;padding:7px 0;
    border-top:1px solid #1c1c26;flex-wrap:wrap}
  .row:first-child{border-top:0}
  .row .ep{color:#cfd3dc;min-width:88px;font-size:13px}
  .row .url{color:#5d626c;font-size:12px;word-break:break-all;flex:1;min-width:220px}
  .epsbox{display:flex;gap:8px;margin:6px 0 8px;align-items:center;flex-wrap:wrap}
  .epsbox input{width:150px;background:#101018;border:1px solid var(--line);
    border-radius:6px;padding:5px 9px;color:var(--txt);font-size:13px;outline:none}
  .epsbox span{color:var(--dim);font-size:12px}
  .res{color:#7aa7ff;font-size:12px}
  #dlpanel{display:none;background:var(--card);border:1px solid var(--line);
    border-radius:10px;padding:12px 16px;margin-bottom:14px}
  .dlhead{color:var(--dim);font-size:12px;margin-bottom:8px}
  #dldir{width:300px;background:#101018;border:1px solid var(--line);
    border-radius:6px;padding:4px 9px;color:var(--txt);font-size:12px;outline:none;margin:0 6px}
  .dltask{padding:7px 0;border-top:1px solid #1e1e2a}
  .dltask:first-of-type{border-top:0}
  .dlname{font-size:13px;color:#cfd3dc;margin-bottom:5px;word-break:break-all}
  .ok{color:#5ad08a}.bad{color:#ff8080}.dim{color:var(--dim);font-size:12px}
  .dlbar{height:6px;background:#1e1e2a;border-radius:3px;overflow:hidden;margin:4px 0}
  .dlfill{height:100%;background:var(--acc);border-radius:3px;transition:width .3s}
  button.cp.dlbtn{color:#7aa7ff;border-color:#152642}
  button.cp.dlbtn:hover{background:#152642}
  .empty{color:var(--dim);text-align:center;padding:50px 0;font-size:14px}
  #toast{position:fixed;left:50%;bottom:32px;transform:translateX(-50%);
    background:var(--acc);color:#fff;padding:8px 18px;border-radius:6px;
    font-size:13px;display:none;z-index:9}
  .foot{color:#5d626c;font-size:12px;margin-top:26px;border-top:1px solid var(--line);
    padding-top:12px;line-height:1.9}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid #2c2c38;
    border-top-color:var(--acc);border-radius:50%;animation:r 0.8s linear infinite;
    vertical-align:-2px;margin-right:6px}
  @keyframes r{to{transform:rotate(360deg)}}
</style></head><body>
<div id="wrap">
  <h1>影视检索 · 下载台 <small>本地服务 · 仅本机访问 · 零安装</small></h1>
  <div class="searchbar">
    <input id="wd" placeholder="输入片名回车，例：流浪地球 / 泰坦尼克号 / 狂飙" autofocus>
    <button id="go">搜 索</button>
  </div>
  <div id="status"></div>
  <div id="dlpanel"></div>
  <div id="out"><div class="empty">搜一部试试 —— 直链 mp4 点「下载」浏览器直接另存为，<br>或复制丢给迅雷 / IDM；m3u8 清单可点「播放」直接看。</div></div>
</div>
<div id="toast"></div>
<script>
'use strict';
const $ = s => document.querySelector(s);
const ENC = encodeURIComponent;
let ROWS = [];

function toast(msg){
  const t = $('#toast'); t.textContent = msg; t.style.display = 'block';
  clearTimeout(t._h); t._h = setTimeout(()=>t.style.display='none', 1800);
}
function copyText(txt, msg){
  (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject())
    .then(()=>toast(msg||'已复制'))
    .catch(()=>{
      const ta=document.createElement('textarea'); ta.value=txt;
      document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove(); toast(msg||'已复制');
    });
}
function esc(s){
  return String(s??'').replace(/[&<>"']/g,
    c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function parseEps(spec, total){
  spec=(spec||'').trim().toLowerCase();
  if(!spec||spec==='all'||spec==='全部') return Array.from({length:total},(_,i)=>i+1);
  const out=new Set();
  for(const part of spec.split(/[,，\\s]+/)){
    if(!part) continue;
    const m=part.match(/^(\\d+)-(\\d+)$/);
    if(m){for(let i=+m[1];i<=+m[2];i++) if(i>=1&&i<=total) out.add(i);}
    else if(/^\\d+$/.test(part)){const i=+part; if(i>=1&&i<=total) out.add(i);}
  }
  return [...out].sort((a,b)=>a-b);
}
function safeName(title, year, ep, url){
  let ext='.mp4'; const p=(url||'').split('?')[0].toLowerCase();
  for(const e of ['.mp4','.mkv','.avi','.mov','.wmv','.flv','.rmvb','.ts','.m4v'])
    if(p.endsWith(e)) ext=e;
  let s=((title||'')+'('+(year||'')+') '+(ep||''))
    .replace(/[\\\\/:*?"<>|\\r\\n\\t]/g,'_').replace(/[.\\s]+$/,'') || 'video';
  if(!s.toLowerCase().endsWith(ext)) s+=ext;
  return s;
}
function fileUrl(ep, url, row){
  const name = safeName(row.title, row.year, ep, url);
  return '/file?u='+ENC(url)+'&name='+ENC(name);
}
function epKey(ep){
  const m = String(ep||'').match(/\\d+/);
  return m ? +m[0] : 9999;
}
function directLinks(row, spec){
  // 直链 mp4 优先，其次每集 m3u8 清单（迅雷可下，播放器可播），按集数自然排序
  const out = [];
  const seen = new Set();
  (row.down||[]).forEach(d=>{
    if(!seen.has(d[1])){ seen.add(d[1]); out.push({ep:d[0], url:d[1], kind:d[2]}); }
  });
  (row.play||[]).forEach(p=>{
    if(seen.has(p[1])) return;
    seen.add(p[1]);
    const isList = p[1].split('?')[0].toLowerCase().endsWith('.m3u8');
    out.push({ep:p[0], url:p[1], kind: isList ? 'm3u8' : 'link'});
  });
  if(!out.length && row.url)
    out.push({ep:'全片', url:row.url, kind: row.m3u8 ? 'm3u8' : 'link'});
  out.sort((a,b)=>{
    const ka = epKey(a.ep), kb = epKey(b.ep);
    if(ka !== kb) return ka - kb;
    return (a.kind==='file'?0:1) - (b.kind==='file'?0:1);
  });
  const idx = parseEps(spec, out.length);
  return idx.map(i=>out[i-1]);
}

function setStatus(html_){ $('#status').innerHTML = html_; }

async function doSearch(){
  const wd = $('#wd').value.trim();
  if(!wd){ toast('先输入片名'); return; }
  $('#go').disabled = true;
  ROWS = [];
  $('#out').innerHTML = '';
  setStatus('<span class="spin"></span>正在检索 15 个资源源…');
  try{
    const resp = await fetch('/api/search?wd='+ENC(wd));
    if(!resp.ok || !resp.body){ throw new Error('HTTP '+resp.status); }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for(;;){
      const {done, value} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while((i = buf.indexOf('\\n')) >= 0){
        const line = buf.slice(0, i).trim(); buf = buf.slice(i+1);
        if(!line) continue;
        let msg; try{ msg = JSON.parse(line); }catch{ continue; }
        if(msg.t === 'p'){
          const f = Math.round(12*msg.d/Math.max(1,msg.tot));
          setStatus('<span class="bar">'+'█'.repeat(f)+'░'.repeat(12-f)+'</span> '
            + msg.d + '/' + msg.tot + '　' + esc(msg.l) + '　' + msg.c + ' 条　' + msg.e + 's');
        }else if(msg.t === 'n'){
          setStatus('<span class="spin"></span>' + esc(msg.m));
        }else if(msg.t === 'r'){
          renderResults(msg);
        }else if(msg.t === 'err'){
          setStatus(''); toast('搜索出错：'+msg.m);
        }
      }
    }
  }catch(e){
    if(e.name !== 'AbortError') toast('搜索失败：' + e.message);
  }finally{
    $('#go').disabled = false;
    if(!ROWS.length && $('#out').children.length === 0){
      setStatus(''); $('#out').innerHTML = '<div class="empty">没有搜到。试试更短的片名。</div>';
    }
  }
}

function renderResults(msg){
  ROWS = msg.rows || [];
  setStatus('');
  const dist = {};
  ROWS.forEach(r=>{ const q=r.quality||'未标注'; dist[q]=(dist[q]||0)+1; });
  const order=['4K','1080P','720P','蓝光','高清','标清','枪版','未标注'];
  const distTxt = order.filter(k=>dist[k]).map(k=>k+'×'+dist[k]).join('　');
  const srcs = [...new Set(ROWS.flatMap(r=>r.sources||[]))].join('、');
  let head = '<div class="statline">共 '+ROWS.length+' 条结果'
    + (distTxt ? '　｜　画质分布 '+distTxt : '')
    + (srcs ? '　｜　来源 '+esc(srcs) : '') + '</div>';
  head += '<div class="topbtns">'
    + '<button onclick="copyAllDirect()">复制所有结果给迅雷</button>'
    + '<button onclick="exportTxt()">导出所有结果 txt</button></div>';
  const cards = ROWS.map((r,i)=>cardHtml(r,i)).join('');
  $('#out').innerHTML = head + (cards || '<div class="empty">没有搜到。</div>');
  // 直链列表默认全部展开，一眼看到每集的迅雷下载
  ROWS.forEach((_,i)=>toggleLinks(i));
}

function cardHtml(r, i){
  const tags = [];
  if(r.quality && r.quality!=='未标注') tags.push('<span class="tag q">'+esc(r.quality)+'</span>');
  const nFiles = (r.down||[]).filter(d=>d[2]==='file').length;
  const nList = (r.play||[]).length;
  if(nFiles && nList)      tags.push('<span class="tag dl">迅雷直链 '+nFiles+' + 清单 '+nList+'</span>');
  else if(nFiles)          tags.push('<span class="tag dl">迅雷直链 '+nFiles+' 条</span>');
  else if(nList)           tags.push('<span class="tag q2">清单 '+nList+' 集 · 迅雷可下</span>');
  else                     tags.push('<span class="tag no">无下载链接</span>');
  (r.sources||[]).slice(0,4).forEach(s=>tags.push('<span class="tag src">'+esc(s)+'</span>'));
  const metaBits = [r.type, r.area, r.year?('年份 '+r.year):'',
    r.episodes?('共 '+r.episodes+' 集'):'', r.lines?('线路 '+r.lines):'',
    r.score?('匹配 '+r.score):''].filter(Boolean).join('　·　');
  return '<div class="card" id="c'+i+'">'
    + '<h2>'+esc(i+1)+'. '+esc(r.title)+' '+tags.join('')+'</h2>'
    + '<div class="meta">'+esc(metaBits)+'　<span class="res" id="res'+i+'"></span></div>'
    + '<div class="ops">'
    + '<button class="hot" onclick="copyRow('+i+')">全季复制给迅雷</button>'
    + '<button onclick="exportRowTxt('+i+')">全季导出 txt</button>'
    + '<button onclick="toggleLinks('+i+')">逐集列表 ▾</button>'
    + (r.m3u8 ? '<button onclick="playRow('+i+')">播放</button>' : '')
    + (r.m3u8 ? '<button onclick="queryRes('+i+')">查真实画质</button>' : '')
    + '</div>'
    + '<div class="links" id="lk'+i+'" style="display:none"></div>'
    + '</div>';
}

function thunderOf(u){
  // 迅雷专链：thunder:// + base64("AA"+url+"ZZ")，点击直接唤起迅雷新建任务
  return 'thunder://' + btoa(unescape(encodeURIComponent('AA'+u+'ZZ')));
}
function toggleLinks(i, specOverride){
  const box = $('#lk'+i);
  if(box.style.display !== 'none' && specOverride === undefined){
    box.style.display = 'none'; return;
  }
  const r = ROWS[i];
  const specInput = box.querySelector('input');
  const spec = specOverride !== undefined ? specOverride
             : (specInput ? specInput.value : '');
  const links = directLinks(r, spec);
  const btns = links.map((lk,j)=>{
    const tag = {file:'直链文件',m3u8:'清单',magnet:'磁力',ed2k:'电骡',thunder:'迅雷链',ftp:'FTP'}[lk.kind]||'链接';
    if(lk.kind==='file'){
      return '<div class="row"><span class="ep">'+esc(lk.ep)+'</span>'
        + '<a class="dl" href="'+thunderOf(lk.url)+'">迅雷下载</a>'
        + '<a class="dl list" href="'+esc(fileUrl(lk.ep,lk.url,r),true)+'" download>浏览器下载</a>'
        + '<button class="cp dlbtn" onclick="startDl(this.dataset.u,this.dataset.n)" '
        + '      data-u="'+esc(lk.url,true)+'" data-n="'+esc(safeName(r.title,r.year,lk.ep,lk.url),true)+'">下载到本机</button>'
        + '<button class="cp" onclick="copyText(this.dataset.u,this.dataset.m)" data-m="直链已复制" data-u="'+esc(lk.url,true)+'">复制</button>'
        + '<span class="url">'+esc(lk.url)+'</span></div>';
    }
    if(lk.kind==='m3u8'){
      return '<div class="row"><span class="ep">'+esc(lk.ep)+'</span>'
        + '<a class="dl list" href="'+thunderOf(lk.url)+'">迅雷下载</a>'
        + '<button class="cp dlbtn" onclick="startDl(this.dataset.u,this.dataset.n)" '
        + '      data-u="'+esc(lk.url,true)+'" data-n="'+esc(safeName(r.title,r.year,lk.ep,lk.url),true)+'">下载到本机</button>'
        + '<button class="cp" onclick="playUrl(this.dataset.u)">播放</button>'
        + '<button class="cp" onclick="copyText(this.dataset.u,this.dataset.m)" data-m="清单已复制" data-u="'+esc(lk.url,true)+'">复制清单</button>'
        + '<span class="url">'+esc(lk.url)+'</span></div>';
    }
    return '<div class="row"><span class="ep">'+esc(lk.ep)+'</span>'
      + '<button class="cp" onclick="copyText(this.dataset.u,this.dataset.m)" data-m="链接已复制" data-u="'+esc(lk.url,true)+'">复制'+esc(tag)+'</button>'
      + '<span class="url">'+esc(lk.url)+'</span></div>';
  }).join('');
  const eps = (links.length>1)
    ? '<div class="epsbox">选集 <input class="epsInput" data-i="'+i+'" placeholder="如 1-5 或 1,3,7，空=全部" value="'+esc(spec)+'">'
      + '<button class="cp" onclick="setAllEps(this.dataset.i)" data-i="'+i+'">全部</button>'
      + '<span>共 '+links.length+' 条</span></div>'
    : '';
  box.innerHTML = eps + (btns || '<div class="meta">没有链接</div>');
  box.style.display = 'block';
}

function playRow(i){ playUrl(ROWS[i].url); }
function playUrl(u){ window.open('/play?u='+ENC(u), '_blank'); }

async function queryRes(i){
  const el = $('#res'+i); const r = ROWS[i];
  el.textContent = '查询中…';
  try{
    const resp = await fetch('/api/info?u='+ENC(r.url));
    const data = await resp.json();
    el.textContent = '真实分辨率：' + data.res;
  }catch{ el.textContent = '查询失败'; }
}

function collectDirect(rowList){
  // mp4 直链 + 每集 m3u8 都收（迅雷对两者都能建任务）
  const out = new Set();
  rowList.forEach(r=>directLinks(r,'').forEach(lk=>{
    if(['file','magnet','ed2k','thunder','ftp','m3u8'].includes(lk.kind)) out.add(lk.url);
  }));
  return [...out];
}
function copyAllDirect(){
  const urls = collectDirect(ROWS);
  if(!urls.length){ toast('没有直链可复制'); return; }
  copyText(urls.join('\\r\\n'), '已复制 '+urls.length+' 条直链，去迅雷整批粘贴');
}
function copyRow(i){
  const r = ROWS[i];
  const urls = collectDirect([r]);
  if(!urls.length){ toast('没有可复制的链接'); return; }
  copyText(urls.join('\\r\\n'),
    '已复制 '+urls.length+' 条 → 迅雷「新建任务」Ctrl+V 整批开下');
}
let dlTimer = null;
function dlDir(){
  const el = document.getElementById('dldir');
  return el ? el.value.trim() : '';
}
function saveDir(){
  const v = dlDir();
  localStorage.setItem('fsdl_dir', v);
  toast(v ? '保存位置已记住：' + v : '已恢复默认位置（downloads 文件夹）');
}
function startDl(u, n){
  const dir = dlDir();
  fetch('/api/download?u='+ENC(u)+'&name='+ENC(n)+'&dir='+ENC(dir))
    .then(r=>r.json())
    .then(d=>{ toast('已加入下载队列：'+d.name+' → '+d.dir); pollDl(); })
    .catch(()=>toast('任务创建失败'));
}
function pollDl(){
  if(dlTimer) return;
  refreshDl();
  dlTimer = setInterval(refreshDl, 1200);
}
function refreshDl(){
  fetch('/api/downloads').then(r=>r.json()).then(d=>{
    const ts = d.tasks||[];
    const box = document.getElementById('dlpanel');
    if(!box) return;
    box.style.display='block';
    const savedDir = localStorage.getItem('fsdl_dir') || '';
    const list = ts.length ? ts.map(t=>{
          let st;
          if(t.status==='done') st='<span class="ok">✅ 完成</span>';
          else if(t.status==='error') st='<span class="bad">❌ 失败：'+esc(t.note||'')+'</span>';
          else if(t.status==='queued') st='<span class="dim">排队中…</span>';
          else st='<span class="dim">'+esc(t.note||'下载中…')+'</span>';
          const pct = t.status==='done' ? 100 : (t.pct||0);
          const size = t.total ? '<span class="dim">'+(t.done/1048576).toFixed(1)+' / '+(t.total/1048576).toFixed(1)+' MB</span>' : '';
          return '<div class="dltask"><div class="dlname">'+esc(t.name)+'　'+st+'　'+size+'</div>'
            + '<div class="dlbar"><div class="dlfill" style="width:'+pct+'%"></div></div></div>';
        }).join('') : '<div class="dim" style="padding:6px 0">暂无下载任务 —— 在下面的结果里点「下载到本机」即可</div>';
    box.innerHTML = '<div class="dlhead">本机下载 · 保存位置：'
      + '<input id="dldir" placeholder="留空 = downloads 文件夹（极空间可设为同步目录）" value="'+esc(savedDir)+'" '
      + 'onchange="saveDir()"> <button class="cp" onclick="saveDir()">记住</button></div>'
      + list;
  }).catch(()=>{});
}
pollDl();
function txtTitle(r){
  return ((r.title||'video')+'('+(r.year||'')+')_全季链接')
    .replace(/[\\\\/:*?"<>|\\r\\n\\t]/g,'_');
}
function exportRowTxt(i){
  const r = ROWS[i];
  const urls = collectDirect([r]);
  if(!urls.length){ toast('没有可导出的链接'); return; }
  const blob = new Blob([urls.join('\\r\\n')+'\\r\\n'], {type:'text/plain;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = txtTitle(r) + '.txt'; a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href), 5000);
  toast('已导出 '+urls.length+' 条 → 迅雷新建任务里全选复制粘贴即可整批开下');
}
function setAllEps(i){ toggleLinks(+i, ''); }
function exportTxt(){
  const urls = collectDirect(ROWS);
  if(!urls.length){ toast('没有直链可导出'); return; }
  const blob = new Blob([urls.join('\\r\\n')+'\\r\\n'], {type:'text/plain;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '下载链接.txt'; a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href), 5000);
  toast('已导出 '+urls.length+' 条');
}

$('#go').onclick = doSearch;
$('#wd').addEventListener('keydown', e=>{ if(e.key==='Enter') doSearch(); });
</script>
</body></html>"""


def _run_task(srv, tid: str) -> None:
    """后台执行一个「下载到本机」任务，进度写回 srv.dl_tasks[tid]。"""
    t = srv.dl_tasks[tid]
    with srv.dl_sem:
        if t["status"] == "cancelled":
            return
        def prog(done, total, note=""):
            t["done"], t["total"], t["note"] = done, total, note
            t["pct"] = round(done / total * 100, 1) if total else 0.0
        try:
            t["status"] = "running"
            url = t["url"]
            out = Path(t["path"])
            if url.split("?")[0].lower().endswith(".m3u8"):
                # m3u8：ffmpeg 在 → mp4；否则纯 Python 拼 ts（产物路径回写）
                if downloader.has_ffmpeg():
                    rc = downloader.download_with_ffmpeg(
                        url, out, player._referer_for(url), player.UA)
                    t["status"] = "done" if rc == 0 else f"ffmpeg 返回 {rc}"
                else:
                    ts = out.with_suffix(".ts")
                    rc = downloader.download_python(url, ts, on_progress=prog)
                    t["status"] = "done" if rc == 0 else f"部分分片失败(码 {rc})，文件可能不完整"
                    t["path"] = str(ts)
            else:
                downloader.download_direct(url, out, referer=t.get("referer") or "",
                                           on_progress=prog)
                t["status"] = "done"
        except Exception as exc:
            t["status"] = "error"
            t["note"] = f"{type(exc).__name__}: {exc}"


class _Handler(BaseHTTPRequestHandler):
    server_version = "FilmWeb/1.0"

    def log_message(self, fmt, *args):
        pass

    def _plain(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200):
        self._plain(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8")
    # ---- 搜索：NDJSON 流式（进度 → 结果）----
    def _api_search(self, name: str):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def send(obj):
            try:
                self.wfile.write(
                    (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False

        def on_progress(done, total, label, count, elapsed):
            send({"t": "p", "d": done, "tot": total,
                  "l": label, "c": count, "e": elapsed})

        def on_notice(msg):
            send({"t": "n", "m": msg})

        try:
            rows = self.server.search_fn(name, self.server.cfg,
                                         show_progress=False,
                                         on_progress=on_progress,
                                         on_notice=on_notice)
            send({"t": "r", "rows": rows})
        except Exception as exc:
            send({"t": "err", "m": f"{type(exc).__name__}: {exc}"})

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._plain(200, self.server.page.encode("utf-8"),
                        "text/html; charset=utf-8")
            return

        if path == "/api/search":
            name = (params.get("wd") or [""])[0].strip()
            if not name:
                self._json({"t": "err", "m": "missing wd"}, 400)
                return
            self._api_search(name)
            return

        if path == "/api/info":
            target = (params.get("u") or [""])[0]
            if not target:
                self._json({"res": "missing u"}, 400)
                return
            try:
                res = self.server.info_fn(target)
            except Exception as exc:
                res = f"查询失败 {type(exc).__name__}"
            self._json({"res": res})
            return

        if path == "/api/ping":
            self._json({"ok": True})
            return

        if path == "/api/download":
            target = (params.get("u") or [""])[0]
            name = (params.get("name") or ["video.mp4"])[0]
            dirp = (params.get("dir") or [""])[0].strip()
            if not target:
                self._json({"err": "missing u"}, 400)
                return
            base = Path(dirp) if dirp else Path(self.server.download_dir)
            base.mkdir(parents=True, exist_ok=True)
            tid = uuid.uuid4().hex[:12]
            out = base / name
            self.server.dl_tasks[tid] = {
                "id": tid, "url": target, "name": name, "path": str(out),
                "referer": player._referer_for(target),
                "status": "queued", "done": 0, "total": 0, "pct": 0.0, "note": "",
            }
            threading.Thread(target=_run_task, args=(self.server, tid),
                             daemon=True).start()
            self._json({"id": tid, "name": name, "dir": str(base)})
            return

        if path == "/api/downloads":
            tasks = sorted(self.server.dl_tasks.values(),
                           key=lambda t: t["id"], reverse=True)
            self._json({"tasks": tasks})
            return

        if path == "/file":
            target = (params.get("u") or [""])[0]
            if not target:
                self._plain(400, b"missing u", "text/plain; charset=utf-8")
                return
            name = (params.get("name") or ["video.mp4"])[0]
            webdl.proxy_file(self, target, name)
            return

        if path == "/play":
            target = (params.get("u") or [""])[0]
            if not target:
                self._plain(400, b"missing u", "text/plain; charset=utf-8")
                return
            html = (player.PLAYER_HTML
                    .replace("__PROXY__", "/proxy?u=" + urllib.parse.quote(target, safe=""))
                    .replace("__RAW__", target)
                    .replace("__DL__", "/dl?u=" + urllib.parse.quote(target, safe=""))
                    .replace("__URL__", target))
            self._plain(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/proxy":
            target = (params.get("u") or [""])[0]
            if not target:
                self._plain(400, b"missing u", "text/plain; charset=utf-8")
                return
            try:
                status, headers, body = player._fetch(target)
            except Exception as exc:
                self._plain(502, f"上游错误: {exc}".encode("utf-8"),
                            "text/plain; charset=utf-8")
                return
            ctype = headers.get("Content-Type", "")
            is_m3u8 = ("mpegurl" in ctype.lower()) or \
                target.split("?")[0].endswith(".m3u8")
            if is_m3u8:
                text = player.rewrite_m3u8(body.decode("utf-8", "replace"), target)
                self._plain(200, text.encode("utf-8"),
                            "application/vnd.apple.mpegurl")
            else:
                self._plain(200, body, ctype or "application/octet-stream")
            return

        if path == "/dl":
            target = (params.get("u") or [""])[0]
            if not target:
                self._plain(400, b"missing u", "text/plain; charset=utf-8")
                return
            name = (params.get("name") or ["video"])[0]
            cmd = player.build_ffmpeg_command(target, name + ".mp4")
            self._plain(200, cmd.encode("utf-8"), "text/plain; charset=utf-8")
            return

        self._plain(404, b"not found", "text/plain; charset=utf-8")


class WebUIServer:
    """网页下载台。search_fn/info_fn 注入，避免与 run.py 循环依赖。"""

    def __init__(self, search_fn, info_fn, cfg: dict, port: int = 0,
                 download_dir=None):
        self.search_fn = search_fn
        self.info_fn = info_fn
        self.cfg = cfg
        self.port = port
        self.download_dir = str(download_dir or Path.cwd() / "downloads")
        Path(self.download_dir).mkdir(parents=True, exist_ok=True)
        self.dl_tasks: dict = {}
        self.dl_sem = threading.Semaphore(2)      # 同时最多 2 个下载任务
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        srv = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        srv.search_fn = self.search_fn
        srv.info_fn = self.info_fn
        srv.cfg = self.cfg
        srv.page = INDEX_HTML
        srv.download_dir = self.download_dir
        srv.dl_tasks = self.dl_tasks
        srv.dl_sem = self.dl_sem
        self._srv = srv
        self.port = srv.server_address[1]
        self._thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        if self._srv:
            self._srv.shutdown()

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"
