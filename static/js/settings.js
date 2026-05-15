// Settings and API Key Management
document.addEventListener('DOMContentLoaded', () => {
    const socket = window.socket;
    
    // UI Elements
    const cardShared = document.getElementById('plan-shared');
    const cardPersonal = document.getElementById('plan-personal');
    const customWrap = document.getElementById('custom-key-wrap');
    const inputKey = document.getElementById('api-custom-key');
    const btnSave = document.getElementById('btn-save-settings');
    
    let currentMode = 'default';
    let originalKey = '';
    let _hasSharedKey = true; // Local cache

    socket.on('api_settings', (data) => {
        currentMode = data.api_mode || 'default';
        originalKey = data.custom_api_key || '';
        _hasSharedKey = data.has_shared_key;
        
        // Always update the input value to prevent "ghost" masks from lingering
        inputKey.value = data.custom_api_key || '';
        
        updateUI(currentMode, _hasSharedKey);
        
        // Only show "Saved" state if there is actually a key present
        if (data.custom_api_key) {
            updateSaveButtonState(false);
        } else {
            updateSaveButtonState(true);
        }
    });

    function updateUI(mode, hasSharedKey = true) {
        currentMode = mode;
        
        // Shared Card Logic
        if (!hasSharedKey) {
            cardShared.classList.add('disabled');
            cardShared.style.opacity = '0.5';
            cardShared.style.pointerEvents = 'none';
            cardShared.querySelector('.ha-plan-status').textContent = 'Not Configured';
            // Force user to personal if shared isn't there
            if (mode === 'default') mode = 'custom';
        } else {
            cardShared.classList.remove('disabled');
            cardShared.style.opacity = '1';
            cardShared.style.pointerEvents = 'all';
        }

        if (mode === 'default') {
            cardShared.classList.add('active');
            cardPersonal.classList.remove('active');
            if (hasSharedKey) cardShared.querySelector('.ha-plan-status').textContent = 'Active';
            cardPersonal.querySelector('.ha-plan-status').textContent = 'Select';
            customWrap.classList.add('hidden');
            btnSave.classList.add('hidden');
        } else {
            cardPersonal.classList.add('active');
            cardShared.classList.remove('active');
            cardPersonal.querySelector('.ha-plan-status').textContent = 'Active';
            if (hasSharedKey) cardShared.querySelector('.ha-plan-status').textContent = 'Select';
            customWrap.classList.remove('hidden');
            btnSave.classList.remove('hidden');
        }
    }

    function updateSaveButtonState(isDirty) {
        const val = inputKey.value.trim();
        // If it's dirty OR the input is currently empty, show the active "Save" state
        if (isDirty || !val) {
            btnSave.classList.remove('saved-success');
            btnSave.textContent = (!val && originalKey) ? 'Remove Personal Key' : 'Save Personal Key';
            btnSave.style.opacity = '1';
            btnSave.style.pointerEvents = 'auto';
        } else {
            btnSave.classList.add('saved-success');
            btnSave.textContent = 'Key Saved ✓';
            btnSave.style.opacity = '0.9';
            btnSave.style.pointerEvents = 'none';
        }
    }

    cardShared.onclick = () => {
        if (currentMode === 'default' || !_hasSharedKey) return;
        
        updateUI('default', _hasSharedKey);
        socket.emit('update_api_settings', {
            api_mode: 'default',
            custom_api_key: inputKey.value.trim()
        });
        showToast('Switched to Shared AI');
    };

    cardPersonal.onclick = () => {
        if (currentMode === 'custom') return;
        updateUI('custom', _hasSharedKey);
        
        // If we already have a key saved, auto-switch the mode on the server
        if (originalKey) {
            socket.emit('update_api_settings', {
                api_mode: 'custom',
                custom_api_key: originalKey
            });
            showToast('Switched to Personal AI');
            updateSaveButtonState(false);
        }
    };

    inputKey.oninput = () => {
        const val = inputKey.value.trim();
        const isDirty = val !== originalKey && !val.includes('*');
        updateSaveButtonState(isDirty);
    };

    btnSave.onclick = () => {
        const keyVal = inputKey.value.trim();
        if (!keyVal) {
            if (confirm('Are you sure you want to remove your Personal API Key and switch back to Shared AI?')) {
                socket.emit('update_api_settings', {
                    api_mode: 'default',
                    custom_api_key: ''
                });
                showToast('Personal Key Removed');
                updateUI('default', _hasSharedKey);
            }
            return;
        }

        socket.emit('update_api_settings', {
            api_mode: 'custom',
            custom_api_key: keyVal
        });
        showToast('Personal API Key Saved');
    };
});
