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

    // ----------------------------------------------------------------------
    // Global AI Priority List
    // ----------------------------------------------------------------------
    const priorityListEl = document.getElementById('ai-priority-list');
    let priorityAutomations = [];

    // We can listen to the same automations_list event as automation.js
    socket.on("automations_list", (list) => {
        if (!list) return;
        priorityAutomations = [...list];
        // Sort by ai_priority ascending
        priorityAutomations.sort((a, b) => (a.ai_priority ?? 99999) - (b.ai_priority ?? 99999));
        renderPriorityList();
    });

    socket.on("automation_update", (data) => {
        if (!data || !data.automation) return;
        const auto = data.automation;
        const idx = priorityAutomations.findIndex(a => a.id === auto.id);
        if (idx !== -1) {
            priorityAutomations[idx] = auto;
        } else {
            priorityAutomations.push(auto);
            priorityAutomations.sort((a, b) => (a.ai_priority ?? 99999) - (b.ai_priority ?? 99999));
        }
        renderPriorityList();
    });

    socket.on("automation_deleted", (data) => {
        if (!data || !data.id) return;
        priorityAutomations = priorityAutomations.filter(a => a.id !== data.id);
        renderPriorityList();
        // The deleted item is naturally removed from the priority array. 
        // We can optionally emit the new map, but we don't strictly have to because 
        // the server also deleted the row in DB. We will just let the DB handle deletion.
    });

    function renderPriorityList() {
        if (!priorityListEl) return;
        priorityListEl.innerHTML = '';
        
        if (priorityAutomations.length === 0) {
            priorityListEl.innerHTML = '<div style="color:var(--ha-text-secondary); font-size:13px; font-style:italic; padding:10px;">No automations found.</div>';
            return;
        }

        priorityAutomations.forEach((auto, index) => {
            const isAIEnabled = !!auto.ai_enabled;
            
            const row = document.createElement('div');
            row.style.cssText = `
                display: flex;
                align-items: center;
                background: rgba(0,0,0,0.2);
                border: 1px solid var(--ha-divider);
                border-radius: 8px;
                padding: 10px 12px;
                gap: 12px;
                transition: background 0.2s;
            `;

            // Number/Rank
            const rank = document.createElement('div');
            rank.style.cssText = 'font-weight: 700; color: var(--ha-primary); width: 20px; text-align: center;';
            rank.textContent = index + 1;
            
            // Info
            const info = document.createElement('div');
            info.style.cssText = 'flex: 1; display: flex; flex-direction: column; gap: 4px;';
            
            const name = document.createElement('div');
            name.style.cssText = 'font-weight: 500; font-size: 14px; color: var(--ha-text);';
            name.textContent = auto.name || 'Unnamed';
            
            const status = document.createElement('div');
            status.style.cssText = `font-size: 12px; color: ${isAIEnabled ? '#4CAF50' : 'var(--ha-text-secondary)'}; display: flex; align-items: center; gap: 4px;`;
            if (isAIEnabled) {
                status.innerHTML = `<span class="material-symbols-outlined" style="font-size:14px;">psychology</span> AI Enabled`;
            } else {
                status.innerHTML = `<span class="material-symbols-outlined" style="font-size:14px;">psychology_alt</span> AI Disabled`;
            }

            info.appendChild(name);
            info.appendChild(status);

            // Controls
            const controls = document.createElement('div');
            controls.style.cssText = 'display: flex; flex-direction: column; gap: 2px;';

            const btnUp = document.createElement('button');
            btnUp.className = 'ha-icon-btn';
            btnUp.style.cssText = 'padding: 4px; border-radius: 4px;';
            btnUp.innerHTML = '<span class="material-symbols-outlined" style="font-size:18px;">keyboard_arrow_up</span>';
            btnUp.disabled = index === 0;
            if (btnUp.disabled) btnUp.style.opacity = '0.3';
            
            const btnDown = document.createElement('button');
            btnDown.className = 'ha-icon-btn';
            btnDown.style.cssText = 'padding: 4px; border-radius: 4px;';
            btnDown.innerHTML = '<span class="material-symbols-outlined" style="font-size:18px;">keyboard_arrow_down</span>';
            btnDown.disabled = index === priorityAutomations.length - 1;
            if (btnDown.disabled) btnDown.style.opacity = '0.3';

            btnUp.onclick = () => moveAutomation(index, -1);
            btnDown.onclick = () => moveAutomation(index, 1);

            controls.appendChild(btnUp);
            controls.appendChild(btnDown);

            row.appendChild(rank);
            row.appendChild(info);
            row.appendChild(controls);

            priorityListEl.appendChild(row);
        });
    }

    function moveAutomation(index, direction) {
        const newIndex = index + direction;
        if (newIndex < 0 || newIndex >= priorityAutomations.length) return;

        // Swap array elements
        const temp = priorityAutomations[index];
        priorityAutomations[index] = priorityAutomations[newIndex];
        priorityAutomations[newIndex] = temp;

        // Re-render UI instantly for responsive feel
        renderPriorityList();

        // Build priority map and send to server
        const priorityMap = {};
        priorityAutomations.forEach((auto, idx) => {
            priorityMap[auto.id] = idx;
            auto.ai_priority = idx; // Update local state
        });

        socket.emit("update_ai_priority", { priorities: priorityMap });
    }
});
