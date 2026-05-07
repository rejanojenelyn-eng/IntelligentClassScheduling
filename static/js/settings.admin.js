const settingsWrapper = document.getElementById('settingsWrapper');
const IS_LOCKED = settingsWrapper.getAttribute('data-locked') === 'true';

function closeModal(id) { document.getElementById(id).style.display = 'none'; }
function openModal(id)  { document.getElementById(id).style.display = 'flex'; }

function prepareAY(el) {
    const d = el.dataset;
    document.getElementById('ay_modal_title').innerText = "EDIT ACADEMIC YEAR";
    document.getElementById('ay_year_start').value = d.start;
    document.getElementById('ay_year_end').value   = d.end;
    document.getElementById('ay_s1s').value = (d.s1s && d.s1s !== 'None') ? d.s1s : '';
    document.getElementById('ay_s1e').value = (d.s1e && d.s1e !== 'None') ? d.s1e : '';
    document.getElementById('ay_s2s').value = (d.s2s && d.s2s !== 'None') ? d.s2s : '';
    document.getElementById('ay_s2e').value = (d.s2e && d.s2e !== 'None') ? d.s2e : '';
    document.getElementById('ay_s3s').value = (d.s3s && d.s3s !== 'None') ? d.s3s : '';
    document.getElementById('ay_s3e').value = (d.s3e && d.s3e !== 'None') ? d.s3e : '';
    openModal('modalAY');
}

function openAYModal() {
    document.getElementById('ay_modal_title').innerText = "ADD ACADEMIC YEAR";
    document.querySelectorAll('#modalAY input').forEach(i => i.value = '');
    openModal('modalAY');
}

function prepareEmp(el) {
    const d = el.dataset;
    document.getElementById('et_name_label').innerText     = d.name;
    document.getElementById('modal_et_id').value           = d.id;
    document.getElementById('modal_et_reg').value          = (d.rl  && d.rl  !== 'None') ? d.rl  : '';
    document.getElementById('modal_et_pt').value           = (d.ptl && d.ptl !== 'None') ? d.ptl : '';
    document.getElementById('modal_et_sub').value          = (d.sub && d.sub !== 'None') ? d.sub : '';
    document.getElementById('modal_et_rs').value           = (d.rs  && d.rs  !== 'None') ? d.rs  : '';
    document.getElementById('modal_et_re').value           = (d.re  && d.re  !== 'None') ? d.re  : '';
    document.getElementById('modal_et_ps').value           = (d.ps  && d.ps  !== 'None') ? d.ps  : '';
    document.getElementById('modal_et_pe').value           = (d.pe  && d.pe  !== 'None') ? d.pe  : '';
    openModal('modalEditEmpType');
}

function prepareDesig(el) {
    const d = el.dataset;
    document.getElementById('des_name_title').innerText = d.name;
    document.getElementById('modal_des_id').value       = d.id;
    document.getElementById('modal_des_reg').value      = d.rl;
    document.getElementById('modal_des_night').value    = d.nt;
    openModal('modalEditDesig');
}

function openAddDesigModal() { openModal('modalAddDesig'); }
