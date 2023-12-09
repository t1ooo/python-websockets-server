const socket = new WebSocket("ws://127.0.0.1:8080");

socket.addEventListener("open", (event) => {
  console.log("connect");
});

socket.addEventListener("message", (event) => {
  console.log("receive", event.data);
  try {
    data = JSON.parse(event.data)
    for (const v of data) {
      appendMessage(v);
    }
  } catch {
    console.log("fail to decode data")
  }
});

function appendMessage(data) {
  const time = new Date(data.date).toLocaleTimeString();
  const message = data.message;

  const messageEl = document.createElement("pre");
  messageEl.textContent = `${time}: ${message}`;

  const chat = document.getElementById("chat");
  chat.appendChild(messageEl);
}

function handle_message() {
  const input = document.getElementById("message");
  const message = input.value;
  if (message.length == 0) {
    console.log("message is empty, ignore");
    return false;
  }

  input.value = "";

  const data = {
    date: new Date().toISOString(),
    message: message,
  };

  socket.send(JSON.stringify(data));
  console.log("send", data);
  return false;
}
