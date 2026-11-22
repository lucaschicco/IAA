// Botón "Copiar" del link de invitación. Archivo propio y no un script en línea: la CSP solo deja correr lo de
// este origen. Sin este archivo la página sigue sirviendo: el link queda seleccionable en su campo.
//
// Primero se selecciona el link (así, pase lo que pase, queda listo para Ctrl+C) y se copia con execCommand, que
// es sincrónico dentro del clic. La API del portapapeles queda de respaldo: sin foco en la pestaña su promesa
// puede no resolverse nunca, por eso no se la espera para avisar.
document.addEventListener("click", (evento) => {
  const boton = evento.target.closest("[data-copiar]");
  if (!boton) return;
  const campo = document.getElementById(boton.dataset.copiar);
  if (!campo) return;
  campo.focus();
  campo.select();
  let copiado = false;
  try {
    copiado = document.execCommand("copy");
  } catch {
    copiado = false;
  }
  if (!copiado && navigator.clipboard) {
    navigator.clipboard.writeText(campo.value).then(() => avisar(boton), () => {});
    return;
  }
  if (copiado) avisar(boton);
});

function avisar(boton) {
  const rotulo = boton.querySelector("span");
  if (!rotulo) return;
  const original = boton.dataset.rotulo || rotulo.textContent;
  boton.dataset.rotulo = original;
  rotulo.textContent = boton.dataset.copiado || "Copiado";
  setTimeout(() => { rotulo.textContent = original; }, 2000);
}
