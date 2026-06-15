const SEM_LABELS = { A: '1st Semester', B: '2nd Semester', C: 'Summer' };
const ALL_SUB = [
    'grpAY', 'grpSem', 'sgProgram', 'sgYearLevel', 'sgInstructor',
    'sgFacultyType', 'sgFacultyStatus', 'sgSpecialization',
    'sgBuilding', 'sgRoomType', 'sgCurriculum'
];
function openReportModal(reportType) {

    console.log("Modal opened:", reportType);

    document.getElementById("rptType").value = reportType;

    document.getElementById("reportModal").style.display = "block";
}

function closeReportModal() {
    document.getElementById("reportModal").style.display = "none";
}

function applyFilter() {

    const reportType = document.getElementById("rptType").value;

    const ay = document.getElementById("rptAY")?.value || "All";
    const sem = document.getElementById("rptSem")?.value || "All";
    const prog = document.getElementById("rptProg")?.value || "All";

    const params = new URLSearchParams({
        ay: ay,
        sem: sem,
        prog: prog
    });

    window.location.href =
        `/reports/preview/${reportType}?${params.toString()}`;
}