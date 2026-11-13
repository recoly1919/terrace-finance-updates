/* Terrace Finance 2.3 — owner-only verified update console. */
EXTRA.tr.updates='Güncellemeler';
EXTRA.zh.updates='系统更新';
const UTXT={
 tr:{title:'Güvenli Güncellemeler',intro:'Yeni sürümler imza ve dosya bütünlüğü doğrulanmadan kurulmaz. Kurulumdan önce veritabanı ve belgeler yedeklenir.',current:'Kurulu sürüm',supervisor:'Güncelleme yöneticisi',active:'Etkin',inactive:'Etkin değil',server:'Yayın sunucusu',configured:'Bağlı',unconfigured:'Henüz bağlanmadı',feed:'Manifest HTTPS adresi',pub:'Yayıncı Ed25519 açık anahtarı (base64)',save:'Sunucuyu kaydet',check:'Yeni sürümü kontrol et',install:'Doğrula, indir ve güncelle',rollbacks:'Geri dönüş noktaları',rollback:'Bu sürüme geri dön',version:'Sürüm',none:'Henüz güncelleme yedeği yok',warning:'İlk kurulumdan sonra yeni sürümleri yayımlayacağınız sabit bir HTTPS adresi ve yalnızca yayıncıda tutulan özel imza anahtarı gerekir. Bu paket bir yayın sunucusunu otomatik olarak açmaz.',manual:'Yayın sunucusu hazır değilse şimdilik eski indirme yöntemi çalışmaya devam eder.',automatic:'Bu uygulama Mac üzerinde başlatıcı aracılığıyla çalıştırıldığında kendi kendini güvenli biçimde yeniden başlatır.',ok:'Sonuç',noRelease:'Yeni sürüm bulunamadı',restart:'Kurulum başlatıldı. Uygulama yeniden başlayacak…',confirm:'Güncellemeden önce otomatik yedek alınacak. Şimdi kuruluma başlansın mı?',confirmRollback:'Önceki program sürümüne dönülecek. Finans kayıtları silinmez; uyumsuz veritabanı değişikliklerinde yeni sürüme dönmeniz gerekebilir. Devam edilsin mi?',needServer:'Önce HTTPS yayın sunucusunu ve açık anahtarını tanımlayın.',checking:'Sunucu kontrol ediliyor…',installing:'Güncelleme doğrulanıyor ve kuruluyor…',restartDone:'Uygulama yeniden başlatıldı. Sayfa yenileniyor…',state:'Son işlem durumu',status_idle:'Hazır',status_installed:'Kuruldu',status_rolled_back:'Geri alındı',status_failed_rolled_back:'Başarısız güncelleme otomatik geri alındı',status_critical:'Kritik hata: Elle inceleme gerekli',guide:'Yayınlama kılavuzu paket içindeki RELEASE_SERVER.md dosyasında.'},
 zh:{title:'安全更新',intro:'只安装经数字签名与文件完整性验证的版本。更新前备份数据库与上传文件。',current:'当前版本',supervisor:'更新管理器',active:'运行中',inactive:'未运行',server:'发布服务器',configured:'已连接',unconfigured:'尚未连接',feed:'HTTPS 更新清单地址',pub:'发布方 Ed25519 公钥（base64）',save:'保存服务器设置',check:'检查新版本',install:'验证、下载并更新',rollbacks:'可恢复版本',rollback:'恢复到此版本',version:'版本',none:'还没有恢复点',warning:'必须先提供 HTTPS 发布地址和仅由发布方保管的签名私钥。此软件包不会自行部署远程服务器。',manual:'尚未配置更新服务器时，请继续使用手动安装方式。',automatic:'通过 Mac 启动器运行时，应用会安全重启。',ok:'状态',noRelease:'没有新版本',restart:'开始更新，应用将重启…',confirm:'更新前会自动备份数据。现在安装吗？',confirmRollback:'将恢复旧程序版本。财务数据不会被主动清除，数据库不兼容时可能需要重新安装新版本。是否继续？',needServer:'请先配置 HTTPS 地址和公钥。',checking:'正在检查服务器…',installing:'正在验证并安装…',restartDone:'应用已重新启动，正在刷新…',state:'上次操作状态',status_idle:'就绪',status_installed:'已安装',status_rolled_back:'已回滚',status_failed_rolled_back:'失败并已自动回滚',status_critical:'严重错误，需要人工检查',guide:'发布说明位于压缩包的 RELEASE_SERVER.md。'}
};
let UDATA=null, URELEASE=null, UPOLL=null;
const ut=k=>UTXT[lang]?.[k]||UTXT.tr[k]||k;
const updateText=(x)=>esc(x||'');
function updatesView(){
  if(me?.role!=='owner')return `<div class="section">${lang==='zh'?'仅限管理员':'Yalnızca yönetici'}</div>`;
  queueMicrotask(loadUpdateStatus);
  return `<div class="update-shell">
    <div class="update-hero"><span class="update-icon"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v11m0 0 4-4m-4 4-4-4M4 17v3h16v-3"/></svg></span><div><h2>${ut('title')}</h2><p>${ut('intro')}</p></div></div>
    <div id="updateMain" class="update-section"><p class="muted">${ut('checking')}</p></div>
  </div>`;
}
async function loadUpdateStatus(){
 if(tab!=='updates')return;
 try{UDATA=await api('/update/status');renderUpdateMain()}
 catch(e){document.getElementById('updateMain').textContent=e.message}
}
function updateCard(name,value,sub){return `<div class="update-stat"><span>${name}</span><strong>${value}</strong>${sub?`<small>${sub}</small>`:''}</div>`}
function renderUpdateMain(){
 const box=document.getElementById('updateMain');if(!box||tab!=='updates'||!UDATA)return;
 const state=UDATA.state||{};
 let stateCode='status_'+state.status, stateName=ut(stateCode)===stateCode?state.status:ut(stateCode);
 const back=(UDATA.backups||[]).slice(0,8);
 box.innerHTML=`<div class="update-stats">
   ${updateCard(ut('current'),'V'+updateText(UDATA.version),'Terrace Finance')}
   ${updateCard(ut('supervisor'),UDATA.supervisor?`<span class="update-positive">● ${ut('active')}</span>`:`<span class="update-negative">● ${ut('inactive')}</span>`,ut('automatic'))}
   ${updateCard(ut('server'),UDATA.configured?`<span class="update-positive">${ut('configured')}</span>`:ut('unconfigured'),updateText(UDATA.feed_url||''))}
  </div>
  <div class="update-grid"><section class="update-panel"><h3>${ut('server')}</h3><p class="muted update-hint">${ut('warning')}</p>
   <form id="updateConfigForm" class="update-form" onsubmit="saveUpdateConfig(event)">
     <label>${ut('feed')}<input name="feed" type="url" maxlength="1500" placeholder="https://updates.example.com/stable/manifest.json" value="${updateText(UDATA.feed_url||'')}" required></label>
     <label>${ut('pub')}<textarea name="publicKey" rows="3" maxlength="150" placeholder="Ed25519 public key (base64)" required></textarea></label>
     <button type="submit" class="btn alt">${ut('save')}</button>
   </form>
  </section><section class="update-panel"><h3>${ut('title')}</h3>
   <div class="update-current"><span>${ut('state')}</span><strong>${updateText(stateName||ut('status_idle'))}</strong><small>${updateText(state.detail||'')}</small></div>
   <div id="updateRelease" class="update-release">${URELEASE?`V${updateText(URELEASE.version)} · ${URELEASE.available?'✓':''} ${updateText(URELEASE.notes?.[lang]||'')}`:''}</div>
   <div class="update-actions"><button class="btn alt" onclick="checkUpdate()" ${!UDATA.configured?'disabled':''}>${ut('check')}</button><button class="btn" id="updateInstallBtn" onclick="installUpdate()" ${!UDATA.configured||!UDATA.supervisor?'disabled':''}>${ut('install')}</button></div>
   <p class="muted update-hint">${ut('manual')}</p>
  </section></div>
  <section class="update-panel"><h3>${ut('rollbacks')}</h3>
    ${back.length?back.map(b=>`<div class="update-backup"><div><b>V${updateText(b.version)}</b><small>${updateText(b.created_at?.slice(0,19)||'')} · ${updateText(b.id)}</small></div><button class="btn alt small" onclick="rollbackUpdate('${updateText(b.id)}')" ${!UDATA.supervisor?'disabled':''}>${ut('rollback')}</button></div>`).join(''):`<p class="muted">${ut('none')}</p>`}
  </section><p class="muted update-hint">${ut('guide')}</p>`;
}
async function saveUpdateConfig(ev){
 ev.preventDefault(); const form=ev.target; let data={feed_url:form.elements.feed.value.trim(),public_key:form.elements.publicKey.value.trim()};
 try{await api('/update/config',{method:'POST',body:JSON.stringify(data)});URELEASE=null;toast(lang==='zh'?'已保存':'Sunucu ayarları kaydedildi');await loadUpdateStatus()}
 catch(e){toast(e.message,true)}
}
async function checkUpdate(){
 try{toast(ut('checking'));URELEASE=await api('/update/check',{method:'POST'});renderUpdateMain();if(!URELEASE.available)toast(ut('noRelease'))}
 catch(e){toast(e.message,true)}
}
async function installUpdate(){
 if(!confirm(ut('confirm')))return;
 let b=document.getElementById('updateInstallBtn');if(b)b.disabled=true;
 try{let result=await api('/update/apply',{method:'POST'});if(!result.queued){toast(ut('noRelease'));await loadUpdateStatus();return};toast(ut('restart'));pollRestart(result.version)}
 catch(e){toast(e.message,true);if(b)b.disabled=false}
}
async function rollbackUpdate(id){
 if(!confirm(ut('confirmRollback')))return;
 try{await api('/update/rollback',{method:'POST',body:JSON.stringify({backup_id:id})});toast(ut('restart'));pollRestart(null)}catch(e){toast(e.message,true)}
}
function pollRestart(targetVersion){
 let attempts=0; clearInterval(UPOLL); let initial=UDATA?.version;
 UPOLL=setInterval(async()=>{
  if(tab!=='updates'){clearInterval(UPOLL);return}
  attempts++;
  try{
   let next=await api('/update/status');UDATA=next;
   if(['failed_rolled_back','critical'].includes(next.state?.status)){
     clearInterval(UPOLL);renderUpdateMain();toast(next.state.detail||'Update failed',true);return;
   }
   if((targetVersion&&next.version===targetVersion&&next.state?.status==='installed')||
      (!targetVersion&&next.state?.status==='rolled_back'&&next.version!==initial)){
     clearInterval(UPOLL);toast(ut('restartDone'));location.reload();return;
   }
  }catch(e){/* Server may be stopped during upgrade. */}
  if(attempts>=120){clearInterval(UPOLL);toast(lang==='zh'?'检查更新状态超时，请刷新页面':'Güncelleme durumu uzun sürdü. Sayfayı yenileyip Ayarlar bölümünü kontrol edin.',true)}
 },2000);
}
