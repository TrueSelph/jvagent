// Demo form handler for the pet rock adoption form
// Prevents real submission and shows a fun message

document.addEventListener('DOMContentLoaded', function() {
  const form = document.querySelector('.adopt-form');
  if (form) {
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      const name = form.name.value;
      const rock = form.rock.value;
      alert(`Thank you, ${name}! Your adoption request for ${rock} has been received. Your new friend will be with you soon! 🪨`);
      form.reset();
    });
  }
});
