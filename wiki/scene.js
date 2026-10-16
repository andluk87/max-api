import * as THREE from './vendor/three.module.js';
const host=document.querySelector('#scene'),info=document.querySelector('#node-info');
const nodes=[
 ['Клиент','Web GUI, Python или внешняя система. Ключ передаётся в Authorization: Bearer.'],
 ['HTTPS','Reverse proxy принимает защищённое соединение на 443 и передаёт запрос API на 8087.'],
 ['API','Шлюз проверяет ключ, выбирает аккаунт и выполняет операцию. Очереди сохраняются в SQLite.'],
 ['MAX','Подключённая QR-сессия аккаунта получает события и отправляет сообщения.']
];
let select=()=>{};
nodes.forEach(([label,detail],i)=>{const b=document.createElement('button');b.textContent=label;b.onclick=()=>{info.textContent=detail;select(i);};document.querySelector('.nodes').append(b);});
info.textContent=nodes[0][1];
try{
 const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(40,1,.1,100);camera.position.set(0,2,10);camera.lookAt(0,0,0);
 const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));host.append(renderer.domElement);
 scene.add(new THREE.AmbientLight(0xffffff,2));const light=new THREE.DirectionalLight(0x9bcaff,3);light.position.set(3,5,5);scene.add(light);
 const meshes=nodes.map((item,i)=>{const m=new THREE.Mesh(new THREE.IcosahedronGeometry(.5,1),new THREE.MeshStandardMaterial({color:i===0?0x62dfc7:0x4e78ac,roughness:.4,metalness:.25}));m.position.set((i-1.5)*1.85,0,0);m.userData.index=i;scene.add(m);return m;});
 const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(meshes.map(m=>m.position)),new THREE.LineBasicMaterial({color:0x456080}));scene.add(line);
 function render(){renderer.render(scene,camera);}
 select=i=>{meshes.forEach((m,j)=>{m.material.color.set(j===i?0x62dfc7:0x4e78ac);m.scale.setScalar(j===i?1.2:1);});render();};
 const observer=new ResizeObserver(()=>{const w=host.clientWidth,h=host.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.position.z=w<420?14:10;camera.updateProjectionMatrix();render();});observer.observe(host);
 const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();host.addEventListener('click',e=>{const r=host.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);const hits=ray.intersectObjects(meshes);if(hits.length){const i=hits[0].object.userData.index;select(i);info.textContent=nodes[i][1];}});
 select(0);
}catch(error){host.textContent='3D недоступно. Выбирайте узлы кнопками ниже: клиент → HTTPS → API → MAX.';}
