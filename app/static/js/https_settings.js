async function loadHttpsStatus() {
    const status = document.getElementById('tls-status');
    try {
        const value = await API.get('/api/settings/https');
        status.textContent = `HTTPS ${value.https_active ? '已启用' : '未启用'}；${value.restart_required ? '等待重启生效' : ''} ${value.subject || ''} ${value.expires || ''}`;
    } catch (e) { status.textContent = e.message; }
}
async function importHttpsCertificate() {
    const status = document.getElementById('tls-status');
    const cert = document.getElementById('tls-cert').files[0];
    const key = document.getElementById('tls-key').files[0];
    if (!cert || !key) { status.textContent = '请选择证书和私钥文件'; return; }
    if (cert.size > 65536 || key.size > 32768) { status.textContent = '证书或私钥超过大小限制'; return; }
    if (!confirm('保存证书后需要管理员重启服务才会生效，继续吗？')) return;
    try {
        await API.post('/api/settings/https/certificate', {
            certificate_pem: await cert.text(), private_key_pem: await key.text(),
            password: document.getElementById('tls-password').value || null
        });
        status.textContent = '证书已校验并保存。请在任务结束后重启平台服务，再使用 HTTPS 访问。';
    } catch (e) { status.textContent = e.message; }
    finally {
        document.getElementById('tls-key').value = '';
        document.getElementById('tls-password').value = '';
    }
}
