function showFormSubmissionProgress(event) {
    const submitButton = event.currentTarget.querySelector('button');
    if (!submitButton) {
        return;
    }
    submitButton.disabled = true;
    submitButton.textContent = 'Please wait...';
}

document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', showFormSubmissionProgress);
});
