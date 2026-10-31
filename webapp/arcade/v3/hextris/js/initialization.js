$(document).ready(function() {
	initialize();
});
function initialize(a) {
	window.rush = 1;
	window.lastTime = Date.now();
	window.iframHasLoaded = false;
	window.colors = ["#FF3CAC", "#FFD23F", "#2DE2E6", "#3DFF8F"];
	window.hexColorsToTintedColors = {"#FF3CAC": "rgb(255,157,213)", "#FFD23F": "rgb(255,232,159)", "#2DE2E6": "rgb(150,240,242)", "#3DFF8F": "rgb(158,255,199)"};
	window.rgbToHex = {"rgb(255,60,172)": "#FF3CAC", "rgb(255,210,63)": "#FFD23F", "rgb(45,226,230)": "#2DE2E6", "rgb(61,255,143)": "#3DFF8F"};
	window.rgbColorsToTintedColors = {"rgb(255,60,172)": "rgb(255,157,213)", "rgb(255,210,63)": "rgb(255,232,159)", "rgb(45,226,230)": "rgb(150,240,242)", "rgb(61,255,143)": "rgb(158,255,199)"};

	window.hexagonBackgroundColor = 'rgb(21, 23, 30)';
	window.hexagonBackgroundColorClear = 'rgba(21, 23, 30, 0.5)';
	window.centerBlue = 'rgb(255,210,63)';
	window.angularVelocityConst = 4;
	window.scoreOpacity = 0;
	window.textOpacity = 0;
	window.prevGameState = undefined;
	window.op = 0;
	window.saveState = localStorage.getItem("saveState") || "{}";
	if (saveState !== "{}") {
		op = 1;
	}

	window.textShown = false;
	window.requestAnimFrame = (function() {
		return window.requestAnimationFrame || window.webkitRequestAnimationFrame || window.mozRequestAnimationFrame || function(callback) {
			window.setTimeout(callback, 1000 / framerate);
		};
	})();
	$('#clickToExit').bind('click', toggleDevTools);
	window.settings;
	if (/Android|webOS|iPhone|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)) {
        $('.rrssb-email').remove();
		settings = {
			os: "other",
			platform: "mobile",
			startDist: 227,
			creationDt: 60,
			baseScale: 1.4,
			scale: 1,
			prevScale: 1,
			baseHexWidth: 87,
			hexWidth: 87,
			baseBlockHeight: 20,
			blockHeight: 20,
			rows: 7,
			speedModifier: 0.73,
			speedUpKeyHeld: false,
			creationSpeedModifier: 0.73,
			comboTime: 310
		};
	} else {
		settings = {
			os: "other",
			platform: "nonmobile",
			baseScale: 1,
			startDist: 340,
			creationDt: 9,
			scale: 1,
			prevScale: 1,
			hexWidth: 65,
			baseHexWidth: 87,
			baseBlockHeight: 20,
			blockHeight: 15,
			rows: 8,
			speedModifier: 0.65,
			speedUpKeyHeld: false,
			creationSpeedModifier: 0.65,
			comboTime: 310
		};

	}
	if(/Android/i.test(navigator.userAgent)) {
		settings.os = "android";
	}

	if(navigator.userAgent.match(/iPhone/i) || navigator.userAgent.match(/iPad/i) || navigator.userAgent.match(/iPod/i)){
		settings.os="ios";
	}

	window.canvas = document.getElementById('canvas');
	window.ctx = canvas.getContext('2d');
	window.trueCanvas = {
		width: canvas.width,
		height: canvas.height
	};
	scaleCanvas();

	window.framerate = 60;
	window.history = {};
	window.score = 0;
	window.scoreAdditionCoeff = 1;
	window.prevScore = 0;
	window.numHighScores = 3;

	highscores = [];
	if (localStorage.getItem('highscores')) {
		try {
			highscores = JSON.parse(localStorage.getItem('highscores'));
		} catch (e) {
			highscores = [];
		}
	}
	window.blocks = [];
	window.MainHex;
	window.gdx = 0;
	window.gdy = 0;
	window.devMode = 0;
	window.lastGen = undefined;
	window.prevTimeScored = undefined;
	window.nextGen = undefined;
	window.spawnLane = 0;
	window.importing = 0;
	window.importedHistory = undefined;
	window.startTime = undefined;
	window.gameState;
	setStartScreen();
	if (a != 1) {
		window.canRestart = 1;
		window.onblur = function(e) {
			if (gameState == 1) {
				pause();
			}
		};
		$('#startBtn').off();
		if (settings.platform == 'mobile') {
			$('#startBtn').on('touchstart', startBtnHandler);
		} else {
			$('#startBtn').on('mousedown', startBtnHandler);
		}

		document.addEventListener('touchmove', function(e) {
			e.preventDefault();
		}, false);
		$(window).resize(scaleCanvas);
		$(window).unload(function() {

			if (gameState == 1 || gameState == -1 || gameState === 0) localStorage.setItem("saveState", exportSaveState());
			else localStorage.setItem("saveState", "{}");
		});

		addKeyListeners();


		document.addEventListener("pause", handlePause, false);
		document.addEventListener("backbutton", handlePause, false);
		document.addEventListener("menubutton", handlePause, false); //menu button on android

		setTimeout(function() {
			if (settings.platform == "mobile") {
				try {
					document.body.removeEventListener('touchstart', handleTapBefore, false);
				} catch (e) {

				}

				try {
					document.body.removeEventListener('touchstart', handleTap, false);
				} catch (e) {

				}

				document.body.addEventListener('touchstart', handleTapBefore, false);
			} else {
				try {
					document.body.removeEventListener('mousedown', handleClickBefore, false);
				} catch (e) {

				}

				try {
					document.body.removeEventListener('mousedown', handleClick, false);
				} catch (e) {

				}

				document.body.addEventListener('mousedown', handleClickBefore, false);
			}
		}, 1);
	}
}

function startBtnHandler() {
	setTimeout(function() {
		if (settings.platform == "mobile") {
			try {
				document.body.removeEventListener('touchstart', handleTapBefore, false);
			} catch (e) {

			}

			try {
				document.body.removeEventListener('touchstart', handleTap, false);
			} catch (e) {

			}

			document.body.addEventListener('touchstart', handleTap, false);
		} else {
			try {
				document.body.removeEventListener('mousedown', handleClickBefore, false);
			} catch (e) {

			}

			try {
				document.body.removeEventListener('mousedown', handleClick, false);
			} catch (e) {

			}

			document.body.addEventListener('mousedown', handleClick, false);
		}
	}, 5);

	if (!canRestart) return false;

	if ($('#openSideBar').is(':visible')) {
		$('#openSideBar').fadeOut(150, "linear");
	}

	if (importing == 1) {
		init(1);
		checkVisualElements(0);
	} else {
		resumeGame();
	}
}

function handlePause() {
	if (gameState == 1 || gameState == 2) {
		pause();
	}
}

function handleTap(e) {
	handleClickTap(e.changedTouches[0].clientX, e.changedTouches[0].clientY);
}

function handleClick(e) {
	handleClickTap(e.clientX, e.clientY);
}

function handleTapBefore(e) {
	var x = e.changedTouches[0].clientX;
	var y = e.changedTouches[0].clientY;

	if (x < 120 && y < 83 && $('.helpText').is(':visible')) {
		showHelp();
		return;
	}
}

function handleClickBefore(e) {
	var x = e.clientX;
	var y = e.clientY;

	if (x < 120 && y < 83 && $('.helpText').is(':visible')) {
		showHelp();
		return;
	}
}
