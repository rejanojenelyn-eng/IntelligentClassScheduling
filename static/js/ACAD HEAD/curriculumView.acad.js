// CURRICULUM_ID is defined inline in the HTML template above this script

function updateFilters(type, val) {
    const urlParams = new URLSearchParams(window.location.search);
    urlParams.set(type, val);
    window.location.search = urlParams.toString();
}

function handleFilteredExport() {
    const urlParams = new URLSearchParams(window.location.search);
    const currYear = urlParams.get('year') || '0';
    const currSem = urlParams.get('semester') || 'All';
    window.location.href = `/admin/export/curriculum/${CURRICULUM_ID}?year=${currYear}&semester=${currSem}`;
}
