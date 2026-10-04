(function () {
  "use strict";

  // 1) Vote without reloading the page.
  //    If anything goes wrong the normal form post still works, so the site never depends on JavaScript.
  document.addEventListener("submit", async function (event) {
    var form = event.target;
    var button = event.submitter;
    if (!form.classList.contains("votes") || !button || !window.fetch) { return; }
    event.preventDefault();

    var data = new FormData(form);
    data.set("value", button.value);
    try {
      var response = await fetch(form.action, {
        method: "POST",
        body: data,
        headers: { "X-Requested-With": "fetch" },
        credentials: "same-origin"
      });
      var type = response.headers.get("content-type") || "";
      if (type.indexOf("application/json") === -1) {
        window.location.href = response.url;  // e.g. redirected to the login page
        return;
      }
      var result = await response.json();
      if (!response.ok) {
        showVoteMessage(form, result.error || "Could not save your vote.");
        return;
      }
      form.querySelector(".score").textContent = result.score;
      var up = form.querySelector('button[value="1"]');
      var down = form.querySelector('button[value="-1"]');
      up.classList.toggle("on-up", result.mine === 1);
      down.classList.toggle("on-down", result.mine === -1);
      up.setAttribute("aria-pressed", String(result.mine === 1));
      down.setAttribute("aria-pressed", String(result.mine === -1));
    } catch (error) {
      form.submit();  // network problem: fall back to a normal page load
    }
  });

  function showVoteMessage(form, text) {
    var note = form.querySelector(".vote-msg");
    if (!note) {
      note = document.createElement("span");
      note.className = "vote-msg";
      note.setAttribute("role", "alert");
      form.appendChild(note);
    }
    note.textContent = text;
    window.setTimeout(function () { note.remove(); }, 3500);
  }

  // 2) Live character counter on comment boxes.
  function updateCounter(box) {
    var counter = box.form.querySelector(".counter");
    if (!counter) { return; }
    var max = Number(box.getAttribute("maxlength")) || 500;
    counter.textContent = box.value.length + " / " + max;
    counter.classList.toggle("near-limit", box.value.length > max - 50);
  }
  document.addEventListener("input", function (event) {
    if (event.target.matches("textarea[data-counter]")) { updateCounter(event.target); }
  });

  // 3) Focus the box when "Add a comment" is opened.
  document.addEventListener("toggle", function (event) {
    var details = event.target;
    if (details.matches && details.matches("details.comment-add") && details.open) {
      var box = details.querySelector("textarea");
      box.focus();
      updateCounter(box);
    }
  }, true);

  // 4) Ctrl+Enter (or Cmd+Enter) posts a comment, answer or question from the keyboard.
  document.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && event.target.tagName === "TEXTAREA") {
      var form = event.target.form;
      if (form && form.checkValidity()) { event.preventDefault(); form.requestSubmit(); }
    }
  });
})();
