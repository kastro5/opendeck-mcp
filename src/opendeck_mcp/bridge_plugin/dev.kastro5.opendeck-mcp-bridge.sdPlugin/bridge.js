// OpenDeck MCP bridge: records the geometry of every device OpenDeck reports
// (rows x columns, which is not stored anywhere on disk) into this plugin's
// global settings, where opendeck-mcp reads it from settings/<uuid>.json.
// It has no actions and draws nothing.
"use strict";

const BRIDGE_VERSION = "__BRIDGE_VERSION__";

function connectElgatoStreamDeckSocket(port, uuid, registerEvent, info) {
	const socket = new WebSocket("ws://127.0.0.1:" + port);
	let devices = null; // null until the stored settings have been read
	let lastSent = "";
	const pending = [];

	function merge(id, fields) {
		devices[id] = Object.assign({}, devices[id] || {}, fields);
	}

	function fromInfo(device) {
		const size = device.size || {};
		return {
			name: device.name,
			rows: size.rows,
			columns: size.columns,
			type: device.type,
			connected: true,
			last_seen: new Date().toISOString(),
		};
	}

	function save() {
		const body = JSON.stringify({ schema: 1, bridge_version: BRIDGE_VERSION, devices: devices });
		// OpenDeck echoes setGlobalSettings back as didReceiveGlobalSettings;
		// only writing on a real change keeps that from becoming a loop.
		if (body === lastSent) return;
		lastSent = body;
		socket.send(JSON.stringify({ event: "setGlobalSettings", context: uuid, payload: JSON.parse(body) }));
	}

	function handle(message) {
		if (message.event === "deviceDidConnect") {
			merge(message.device, fromInfo(message.deviceInfo || {}));
			save();
		} else if (message.event === "deviceDidDisconnect") {
			merge(message.device, { connected: false });
			save();
		}
	}

	socket.onopen = function () {
		socket.send(JSON.stringify({ event: registerEvent, uuid: uuid }));
		socket.send(JSON.stringify({ event: "getGlobalSettings", context: uuid }));
	};

	socket.onmessage = function (event) {
		const message = JSON.parse(event.data);
		if (devices === null) {
			if (message.event !== "didReceiveGlobalSettings") {
				pending.push(message);
				return;
			}
			const stored = (message.payload && message.payload.settings) || {};
			devices = {};
			// Devices seen before stay listed, marked disconnected until they report in.
			for (const id of Object.keys(stored.devices || {})) {
				merge(id, Object.assign({}, stored.devices[id], { connected: false }));
			}
			const parsed = typeof info === "string" ? JSON.parse(info) : info || {};
			for (const device of parsed.devices || []) merge(device.id, fromInfo(device));
			pending.splice(0).forEach(handle);
			save();
			return;
		}
		handle(message);
	};
}

const connectOpenActionSocket = connectElgatoStreamDeckSocket;
