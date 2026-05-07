// ACTIVE_ROLE is defined inline in the HTML template above this script

function updateRole(roleName, index, element) {
    document.getElementById('selectedRole').value = roleName;
    const pill = document.getElementById('pill');
    const offset = index * 33.33;
    pill.style.left = `calc(${offset}% + 6px)`;
    document.querySelectorAll('.role-option').forEach(opt => opt.classList.remove('active'));
    element.classList.add('active');
}

document.addEventListener("DOMContentLoaded", function () {
    const roleOptions = document.querySelectorAll('.role-option');
    if (ACTIVE_ROLE === 'Academic Head') {
        updateRole('Academic Head', 1, roleOptions[1]);
    } else if (ACTIVE_ROLE === 'Faculty') {
        updateRole('Faculty', 2, roleOptions[2]);
    } else {
        updateRole('Admin', 0, roleOptions[0]);
    }
});
