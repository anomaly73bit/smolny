/* Smolny — filtri per paese, ricerca, "mostra altre", date relative */
(function () {
    var lang = document.documentElement.lang || "it";
    var feed = document.querySelector(".post-feed");

    // date relative ("3 ore fa" / "3 hours ago")
    var rtf = window.Intl && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat(lang, { numeric: "auto" }) : null;
    function relTime(el) {
        var d = new Date(el.getAttribute("datetime"));
        if (isNaN(d) || !rtf) return;
        var s = (d - Date.now()) / 1000, a = Math.abs(s);
        var out = a < 3600 ? rtf.format(Math.round(s / 60), "minute")
            : a < 86400 ? rtf.format(Math.round(s / 3600), "hour")
            : a < 86400 * 30 ? rtf.format(Math.round(s / 86400), "day")
            : d.toLocaleDateString(lang);
        el.textContent = out;
        el.title = d.toLocaleString(lang);
    }
    document.querySelectorAll(".post-card-meta time, .cover-stats time").forEach(relTime);

    if (!feed) return;
    var cards = Array.prototype.slice.call(feed.querySelectorAll(".post-card"));
    var chips = Array.prototype.slice.call(document.querySelectorAll(".chip"));
    var input = document.querySelector(".search input");
    var moreBtn = document.querySelector(".more-btn");
    var empty = document.querySelector(".feed-empty");
    var step = parseInt(feed.getAttribute("data-page"), 10) || 24;
    var state = { c: "", q: "", shown: step };

    function norm(s) {
        return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    }
    cards.forEach(function (el) { el._q = norm(el.getAttribute("data-q")); });

    function render() {
        var match = cards.filter(function (el) {
            return (!state.c || el.getAttribute("data-c") === state.c) && (!state.q || el._q.indexOf(state.q) !== -1);
        });
        cards.forEach(function (el) { el.classList.add("is-out"); el.classList.remove("is-hidden", "post-card-large"); });
        match.forEach(function (el, i) { if (i < state.shown) el.classList.remove("is-out"); });
        // la prima notizia visibile con foto diventa la card grande
        var first = match[0];
        if (first && !first.classList.contains("poster-card") && window.innerWidth >= 795) first.classList.add("post-card-large");
        if (moreBtn) moreBtn.parentNode.hidden = match.length <= state.shown;
        if (empty) empty.hidden = match.length > 0;
        chips.forEach(function (ch) { ch.classList.toggle("is-on", ch.getAttribute("data-c") === state.c); });
    }

    function fromHash() {
        var m = location.hash.match(/c=([A-Z]+)/);
        state.c = m ? m[1] : "";
        state.shown = step;
        render();
    }

    chips.forEach(function (ch) {
        ch.addEventListener("click", function () {
            var c = ch.getAttribute("data-c");
            history.replaceState(null, "", c ? "#c=" + c : location.pathname);
            state.c = c; state.shown = step; render();
            ch.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
        });
    });
    if (input) {
        var timer;
        input.addEventListener("input", function () {
            clearTimeout(timer);
            timer = setTimeout(function () { state.q = norm(input.value.trim()); state.shown = step; render(); }, 120);
        });
    }
    if (moreBtn) moreBtn.addEventListener("click", function () { state.shown += step; render(); });
    window.addEventListener("hashchange", fromHash);
    fromHash();
})();
