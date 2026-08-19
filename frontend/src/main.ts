import { Chart, registerables } from "chart.js";
import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";

Chart.register(...registerables);

createApp(App).mount("#app");
