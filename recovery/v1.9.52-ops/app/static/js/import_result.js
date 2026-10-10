function showImportDuplicates(result) {
    if (!result.duplicates?.length) return;
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    overlay.innerHTML = `<div class="modal"><div class="modal-header"><h3>导入查重结果</h3><button class="btn" data-close>关闭</button></div><div class="modal-body"><p>${escapeHtml(result.message || '')}</p><p>以下记录未新增、未覆盖。请按记录 ID 核对后在原记录中编辑；已有重复数据未自动删除。</p>${result.duplicates.map(r=>`<p>CSV 第 ${Number(r.line)} 行 → 现有记录 #${r.existing_ids.map(Number).join('、#')}：${escapeHtml(r.reason)}</p>`).join('')}${result.details_truncated?'<p>仅显示前 200 条。</p>':''}</div></div>`;
    overlay.querySelector('[data-close]').onclick=()=>overlay.remove();
    document.body.appendChild(overlay);
}
