<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>NPBC Email Delivery Test</title>
<style>
:root{--black:#050505;--panel:#101010;--gold:#c9a227;--white:#f4f4f4;--muted:#999;--line:#2b2b2b;--green:#16834d;--danger:#a83b3b}
*{box-sizing:border-box}html,body{margin:0;padding:0;min-height:100%}
body{background:radial-gradient(circle at top,#151515 0%,#080808 42%,#050505 100%);color:var(--white);font-family:Arial,Helvetica,sans-serif;padding:20px 12px 40px}
.container{width:100%;max-width:620px;margin:0 auto}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.55)}
.header{padding:28px 24px 25px;background:#080808;border-bottom:1px solid var(--line)}.brand{color:var(--gold);font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase}.title{margin-top:10px;font-size:27px;line-height:1.2;font-weight:700}.subtitle{margin-top:10px;color:var(--muted);font-size:14px;line-height:1.6}
.body{padding:24px}.field{margin-bottom:18px}label{display:block;margin-bottom:8px;color:#d8d8d8;font-size:11px;font-weight:700;letter-spacing:.8px}
input[type=text],input[type=email]{width:100%;min-height:48px;padding:13px 14px;border:1px solid var(--line);border-radius:10px;outline:none;background:#080808;color:#fff;font-size:15px}input[type=text]:focus,input[type=email]:focus{border-color:var(--gold)}
.file-box{padding:14px;background:#080808;border:1px dashed #464646;border-radius:10px}input[type=file]{width:100%;color:#cfcfcf;font-size:13px}.selected-file{display:none;margin-top:10px;padding:10px 12px;background:#111;border-radius:8px;color:#aaa;font-size:12px;line-height:1.5;word-break:break-word}
.send-button{width:100%;min-height:52px;margin-top:5px;border:0;border-radius:11px;background:var(--gold);color:#050505;font-size:15px;font-weight:700;cursor:pointer}.send-button:disabled{opacity:.55;cursor:not-allowed}
.status{display:none;margin-top:18px;padding:15px;border:1px solid var(--line);border-radius:10px;background:#090909;font-size:13px;line-height:1.65;white-space:pre-wrap;word-break:break-word}.status.show{display:block}.status.success{border-color:var(--green)}.status.error{border-color:var(--danger)}
.api-box{margin-top:22px;padding:13px;border:1px solid var(--line);border-radius:10px;background:#090909}.api-label{color:#777;font-size:10px;letter-spacing:1px;text-transform:uppercase}.api-url{margin-top:7px;color:#aaa;font-size:11px;line-height:1.5;word-break:break-all}.security{margin-top:18px;color:#777;font-size:11px;line-height:1.6;text-align:center}.footer{padding:20px 24px;background:#080808;border-top:1px solid var(--line);color:#777;font-size:11px;line-height:1.6;text-align:center}
</style>
</head>
<body>
<div class="container"><div class="card">
<div class="header"><div class="brand">Naija Pocket Business Center</div><div class="title">Email Delivery Test</div><div class="subtitle">Testing only. This page sends a selected document through the standalone Email Test API. It does not use the Payment API.</div></div>
<div class="body">
<div class="field"><label for="recipient">RECIPIENT EMAIL</label><input id="recipient" type="email" placeholder="your@email.com" autocomplete="email"></div>
<div class="field"><label for="customerName">CUSTOMER NAME</label><input id="customerName" type="text" placeholder="Optional"></div>
<div class="field"><label for="service">SERVICE</label><input id="service" type="text" placeholder="e.g. Seminar Paper"></div>
<div class="field"><label for="documentTitle">DOCUMENT TITLE</label><input id="documentTitle" type="text" placeholder="e.g. The Impact of Technology"></div>
<div class="field"><label for="subject">EMAIL SUBJECT</label><input id="subject" type="text" placeholder="Optional — automatic subject if blank"></div>
<div class="field"><label for="document">DOCUMENT TO SEND</label><div class="file-box"><input id="document" type="file" accept=".docx,.pdf,.xlsx,.pptx,.txt"><div id="selectedFile" class="selected-file"></div></div></div>
<button id="sendButton" class="send-button" type="button">SEND TEST EMAIL</button>
<div id="status" class="status"></div>
<div class="api-box"><div class="api-label">Email Test API</div><div id="apiDisplay" class="api-url"></div></div>
<div class="security">The Resend API key is never stored in this page. It remains on the Render server.</div>
</div>
<div class="footer">Fast • Convenient • Open 24/7</div>
</div></div>
<script>
const EMAIL_TEST_API="https://YOUR-EMAIL-TEST-SERVICE.onrender.com";
const recipientInput=document.getElementById("recipient"),customerNameInput=document.getElementById("customerName"),serviceInput=document.getElementById("service"),documentTitleInput=document.getElementById("documentTitle"),subjectInput=document.getElementById("subject"),documentInput=document.getElementById("document"),selectedFile=document.getElementById("selectedFile"),sendButton=document.getElementById("sendButton"),statusBox=document.getElementById("status"),apiDisplay=document.getElementById("apiDisplay");
apiDisplay.textContent=EMAIL_TEST_API;
function showStatus(message,type){statusBox.textContent=message;statusBox.className="status show "+(type||"")}
function formatFileSize(bytes){if(bytes<1024)return bytes+" B";if(bytes<1024*1024)return(bytes/1024).toFixed(1)+" KB";return(bytes/(1024*1024)).toFixed(2)+" MB"}
documentInput.addEventListener("change",function(){if(!documentInput.files.length){selectedFile.style.display="none";selectedFile.textContent="";return}const file=documentInput.files[0];selectedFile.textContent="Selected: "+file.name+"\nSize: "+formatFileSize(file.size);selectedFile.style.display="block"});
sendButton.addEventListener("click",async function(){const recipient=recipientInput.value.trim(),customerName=customerNameInput.value.trim(),service=serviceInput.value.trim(),documentTitle=documentTitleInput.value.trim(),subject=subjectInput.value.trim();if(!recipient){showStatus("Please enter the recipient email address.","error");recipientInput.focus();return}if(!documentInput.files.length){showStatus("Please select a document to send.","error");return}const file=documentInput.files[0];sendButton.disabled=true;sendButton.textContent="SENDING...";showStatus("Sending the document through the Email Test API...","");try{const formData=new FormData();formData.append("recipient_email",recipient);formData.append("customer_name",customerName);formData.append("service",service);formData.append("document_title",documentTitle);formData.append("subject",subject);formData.append("document",file,file.name);const response=await fetch(EMAIL_TEST_API+"/api/test-email",{method:"POST",body:formData});const raw=await response.text();let data;try{data=JSON.parse(raw)}catch{data={detail:raw}}if(!response.ok){throw new Error("HTTP "+response.status+": "+(data.detail||data.message||"The email test failed."))}showStatus("EMAIL TEST SUCCESSFUL\n\nRecipient: "+(data.recipient||recipient)+"\nFile: "+(data.filename||file.name)+"\nMessage ID: "+(data.message_id||"Not returned")+"\n\nThe email has been handed to Resend. Check the recipient inbox.","success")}catch(error){showStatus("EMAIL TEST FAILED\n\n"+(error.message||String(error)),"error")}finally{sendButton.disabled=false;sendButton.textContent="SEND TEST EMAIL"}});
</script>
</body>
</html>
