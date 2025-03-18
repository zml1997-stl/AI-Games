document.addEventListener("DOMContentLoaded", function() {
    // Playlist array
    const playlist = {
        home: '/static/music2.mp3',  // Played on welcome, index, and lobby
        game: '/static/music.mp3'    // Played during game and final scoreboard
    };
    let currentTrack = playlist.home;
    let audioElement = new Audio(currentTrack);
    audioElement.loop = true;
    // Remove fixed volume: audioElement.volume = 0.3;

    // Sound effects
    const selectSound = new Audio('/static/select.mp3');
    const submitSound = new Audio('/static/submit.mp3');
    const correctSound = new Audio('/static/correct.mp3');
    const wrongSound = new Audio('/static/wrong.mp3');
    const roundEndSound = new Audio('/static/round_end.mp3');
    // Remove fixed volumes for sound effects
    // selectSound.volume = 0.5;
    // submitSound.volume = 0.5;
    // correctSound.volume = 0.5;
    // wrongSound.volume = 0.5;
    // roundEndSound.volume = 0.5;

    // DOM elements
    const musicControlBtn = document.getElementById("music-control-btn");
    const muteBtn = document.getElementById("mute-btn");

    // Load saved state from sessionStorage
    const savedState = {
        isPlaying: sessionStorage.getItem("musicIsPlaying") === "true",
        currentTime: parseFloat(sessionStorage.getItem("musicCurrentTime")) || 0,
        isMuted: sessionStorage.getItem("musicIsMuted") === "true",
        // Remove volume from saved state since we want device to handle it
    };

    // Apply saved state
    audioElement.currentTime = savedState.currentTime;
    audioElement.muted = savedState.isMuted;

    // Only play if not muted and was playing before
    if (!savedState.isMuted && savedState.isPlaying) {
        audioElement.play().catch(err => {
            console.log("Autoplay blocked:", err);
        });
    }

    // Toggle music player visibility
    if (musicControlBtn) {
        musicControlBtn.addEventListener("click", function() {
            const musicPlayer = document.getElementById("music-player");
            if (musicPlayer) musicPlayer.classList.toggle("active");
        });
    }

    // Mute/Unmute toggle
    if (muteBtn) {
        muteBtn.addEventListener("click", function() {
            audioElement.muted = !audioElement.muted;
            sessionStorage.setItem("musicIsMuted", audioElement.muted);
            muteBtn.textContent = audioElement.muted ? '🔊' : '🔇';
            muteBtn.classList.toggle("btn-muted", audioElement.muted);
        });
    }

    // Save state periodically
    audioElement.addEventListener("timeupdate", function() {
        sessionStorage.setItem("musicCurrentTime", audioElement.currentTime);
    });

    // Remove volumechange listener since we're not controlling volume
    // audioElement.addEventListener("volumechange", function() {...});

    // Handle page unload
    window.addEventListener("beforeunload", function() {
        sessionStorage.setItem("musicIsPlaying", !audioElement.paused);
        sessionStorage.setItem("musicCurrentTime", audioElement.currentTime);
        sessionStorage.setItem("musicIsMuted", audioElement.muted);
        // Remove volume save: sessionStorage.setItem("musicVolume", audioElement.volume);
    });

    // Switch track function
    function switchTrack(newTrack) {
        if (currentTrack !== newTrack) {
            const wasPlaying = !audioElement.paused;
            audioElement.pause();
            currentTrack = newTrack;
            audioElement.src = currentTrack;
            audioElement.currentTime = 0;
            audioElement.muted = savedState.isMuted;
            if (wasPlaying && !savedState.isMuted) {
                audioElement.play().catch(err => console.log("Switch playback blocked:", err));
            }
        }
    }

    // Expose functions
    window.switchToGameMusic = function() { switchTrack(playlist.game); };
    window.switchToHomeMusic = function() { switchTrack(playlist.home); };
    window.playSelectSound = function() { selectSound.play(); };
    window.playSubmitSound = function() { submitSound.play(); };
    window.playCorrectSound = function() { correctSound.play(); };
    window.playWrongSound = function() { wrongSound.play(); };
    window.playRoundEndSound = function() { roundEndSound.play(); };
    window.muteToggle = function() {
        audioElement.muted = !audioElement.muted;
        sessionStorage.setItem("musicIsMuted", audioElement.muted);
        muteBtn.textContent = audioElement.muted ? '🔊' : '🔇';
        muteBtn.classList.toggle("btn-muted", audioElement.muted);
    };
});