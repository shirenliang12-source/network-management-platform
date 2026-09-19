/* Bounded configuration pages shared by device and backup views. */
let configPreviewRequest = 0;
async function showConfigPage(id, offset = 0) {
    const request = ++configPreviewRequest;
    const content = document.getElementById('configContent');
    document.getElementById('configModal').classList.add('active');
    let controls = document.getElementById('configPageControls');
    if (!controls) {
        controls = document.createElement('div');
        controls.id = 'configPageControls';
        content.before(controls);
    }
    controls.replaceChildren();
    content.textContent = '加载中...';
    try {
        const page = await API.get(`/api/backups/${id}/content?offset=${offset}&limit=32768`);
        if (request !== configPreviewRequest) return;
        content.textContent = page.text;
        content.style.whiteSpace = 'pre';
        content.style.overflow = 'auto';
        const label = document.createElement('span');
        label.textContent = `字符 ${Math.min(offset + 1, page.total)}–${Math.min(offset + page.limit, page.total)} / ${page.total} `;
        controls.append(label);
        for (const [title, next, disabled] of [
            ['上一页', Math.max(0, offset - page.limit), offset === 0],
            ['下一页', offset + page.limit, offset + page.limit >= page.total],
        ]) {
            const button = document.createElement('button');
            button.className = 'btn btn-sm btn-outline';
            button.textContent = title; button.disabled = disabled;
            button.onclick = () => showConfigPage(id, next);
            controls.append(button);
        }
    } catch (error) {
        if (request === configPreviewRequest) content.textContent = '加载失败: ' + error.message;
    }
}
