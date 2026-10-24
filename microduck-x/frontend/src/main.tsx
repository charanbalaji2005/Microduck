import { createRoot } from "react-dom/client";
import App from "./App";
import { installKeyboard } from "./lib/keyboard";
import { connect } from "./lib/ws";
import "./styles.css";

connect();
installKeyboard();
createRoot(document.getElementById("root")!).render(<App />);
