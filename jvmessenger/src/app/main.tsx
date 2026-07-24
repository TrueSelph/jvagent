import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MessengerApp } from "./MessengerApp";
import "./index.css";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MessengerApp />
    </StrictMode>
  );
}
