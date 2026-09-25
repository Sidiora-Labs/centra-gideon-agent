"""Deterministic primitive triangle meshes, rigid part animations and diagnostics."""
import itertools
import json
import math
import struct


def quaternion(euler):
    sx,sy,sz=[math.sin(value/2) for value in euler];cx,cy,cz=[math.cos(value/2) for value in euler]
    return [sx*cy*cz+cx*sy*sz,cx*sy*cz-sx*cy*sz,cx*cy*sz+sx*sy*cz,cx*cy*cz-sx*sy*sz]


def multiply(a,b):
    x,y,z,w=a;u,v,t,s=b
    return [w*u+x*s+y*t-z*v,w*v-x*t+y*s+z*u,w*t+x*v-y*u+z*s,w*s-x*u-y*v-z*t]


def rotate(point,q):
    return multiply(multiply(q,[*point,0]),[-q[0],-q[1],-q[2],q[3]])[:3]


def mesh(part):
    sx,sy,sz=[value/2 for value in part['size']];triangles=[]
    if part['shape']=='box':
        vertices=list(itertools.product((-sx,sx),(-sy,sy),(-sz,sz)))
        faces=((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3))
        for a,b,c,d in faces:
            triangles.extend([[vertices[a],vertices[b],vertices[c]],[vertices[a],vertices[c],vertices[d]]])
    elif part['shape']=='sphere':
        def point(ring,segment):
            latitude=math.pi*ring/8;longitude=2*math.pi*segment/12
            return (sx*math.sin(latitude)*math.cos(longitude),sy*math.cos(latitude),sz*math.sin(latitude)*math.sin(longitude))
        for ring in range(8):
            for segment in range(12):
                a,b,c,d=point(ring,segment),point(ring+1,segment),point(ring+1,segment+1),point(ring,segment+1)
                if ring!=0:triangles.append([a,b,d])
                if ring!=7:triangles.append([b,c,d])
    else:
        for segment in range(12):
            a,b=2*math.pi*segment/12,2*math.pi*(segment+1)/12
            bottom=(sx*math.cos(a),-sy,sz*math.sin(a));next_bottom=(sx*math.cos(b),-sy,sz*math.sin(b))
            top=(bottom[0],sy,bottom[2]);next_top=(next_bottom[0],sy,next_bottom[2])
            triangles.extend([[bottom,top,next_bottom],[top,next_top,next_bottom],[(0,-sy,0),bottom,next_bottom],[(0,sy,0),next_top,top]])
    positions,normals=[],[]
    for a,b,c in triangles:
        u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
        normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        length=math.sqrt(sum(value*value for value in normal)) or 1
        normal=[value/length for value in normal]
        for point in (a,b,c):positions.extend(point);normals.extend(normal)
    return positions,normals


def compile_model(spec):
    document={'asset':{'version':'2.0','generator':'Gideon procedural assemblies'},'scene':0,'scenes':[{'nodes':[]}],'nodes':[],'meshes':[],'materials':[],'buffers':[{'byteLength':0}],'bufferViews':[],'accessors':[],'animations':[]}
    binary=bytearray();parts=spec['parts'];indices={part['id']:i for i,part in enumerate(parts)};bounds={};triangle_count=0
    def accessor(values,width):
        raw=struct.pack('<'+'f'*len(values),*values);view=len(document['bufferViews'])
        document['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(raw)});binary.extend(raw)
        item={'bufferView':view,'componentType':5126,'count':len(values)//width,'type':{1:'SCALAR',3:'VEC3',4:'VEC4'}[width]}
        item['min']=[min(values[i::width]) for i in range(width)];item['max']=[max(values[i::width]) for i in range(width)]
        document['accessors'].append(item);return len(document['accessors'])-1
    def world(point,part):
        point=rotate(point,quaternion(part['rotation']));point=[point[i]+part['position'][i] for i in range(3)]
        return world(point,parts[indices[part['parent_id']]]) if part['parent_id'] else point
    for index,part in enumerate(parts):
        positions,normals=mesh(part);triangle_count+=len(positions)//9
        transformed=[world(positions[i:i+3],part) for i in range(0,len(positions),3)]
        bounds[part['id']]={'min':[min(p[i] for p in transformed) for i in range(3)],'max':[max(p[i] for p in transformed) for i in range(3)]}
        position=accessor(positions,3);normal=accessor(normals,3)
        color=[int(part['color'][i:i+2],16)/255 for i in (1,3,5)]
        document['materials'].append({'pbrMetallicRoughness':{'baseColorFactor':[*color,1],'metallicFactor':0,'roughnessFactor':.7},'doubleSided':True})
        document['meshes'].append({'primitives':[{'attributes':{'POSITION':position,'NORMAL':normal},'material':index}]})
        document['nodes'].append({'name':part['id'],'mesh':index,'translation':part['position'],'rotation':quaternion(part['rotation']),'extras':{'joint':part['joint']}})
    for index,part in enumerate(parts):
        if part['parent_id']:document['nodes'][indices[part['parent_id']]].setdefault('children',[]).append(index)
        else:document['scenes'][0]['nodes'].append(index)
    for clip in spec['clips']:
        part=parts[indices[clip['part_id']]];quaternions=[]
        steps=max(1,math.ceil(abs(clip['to_degrees']-clip['from_degrees'])/90))
        times=[clip['duration_seconds']*i/steps for i in range(steps+1)]
        for i in range(steps+1):
            angle=clip['from_degrees']+(clip['to_degrees']-clip['from_degrees'])*i/steps
            axis=[0,0,0];axis['xyz'.index(clip['axis'])]=math.radians(angle)
            quaternions.extend(multiply(quaternion(part['rotation']),quaternion(axis)))
        times=accessor(times,1);values=accessor(quaternions,4)
        document['animations'].append({'name':clip['name'],'samplers':[{'input':times,'output':values,'interpolation':'LINEAR'}],'channels':[{'sampler':0,'target':{'node':indices[clip['part_id']],'path':'rotation'}}]})
    document['buffers'][0]['byteLength']=len(binary)
    encoded=json.dumps(document,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%4)
    raw=b'glTF'+struct.pack('<II',2,28+len(encoded)+len(binary))+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(binary),0x004e4942)+binary
    findings=[];volume=0
    for part in parts:
        size=part['size'];volume+=math.prod(size)*({'box':1,'sphere':math.pi/6,'cylinder':math.pi/4}[part['shape']])
        if min(size)/max(size)<.02:findings.append({'code':'thin_part','part_ids':[part['id']],'message':'A dimension is below two percent of the largest dimension.'})
    for one,two in itertools.combinations(parts,2):
        a,b=bounds[one['id']],bounds[two['id']]
        if all(min(a['max'][i],b['max'][i])-max(a['min'][i],b['min'][i])>1e-6 for i in range(3)):
            findings.append({'code':'aabb_overlap','part_ids':[one['id'],two['id']],'message':'Part bounding boxes overlap; inspect whether the intersection is intended.'})
    for clip in spec['clips']:
        joint=parts[indices[clip['part_id']]]['joint']
        if min(clip['from_degrees'],clip['to_degrees'])<joint['min_degrees'] or max(clip['from_degrees'],clip['to_degrees'])>joint['max_degrees']:
            findings.append({'code':'clip_outside_joint','part_ids':[clip['part_id']],'message':'Clip '+clip['name']+' exceeds the declared joint range.'})
    total={'min':[min(row['min'][i] for row in bounds.values()) for i in range(3)],'max':[max(row['max'][i] for row in bounds.values()) for i in range(3)]}
    if abs(total['min'][1])>1e-6:findings.append({'code':'off_ground','part_ids':[part['id'] for part in parts if not part['parent_id']],'message':'Lowest model point is not on the ground plane.'})
    return bytes(raw),{'mesh_count':len(parts),'triangle_count':triangle_count,'volume_estimate':volume,'bounds':total,'findings':findings}


def source_module(spec):
    return "import * as THREE from 'three';\nconst spec="+json.dumps(spec)+";\n"+'''export function createAssembly(){
 const scene=new THREE.Group(),parts=new Map();
 for(const p of spec.parts){
  const geometry=p.shape==='box'?new THREE.BoxGeometry(...p.size):p.shape==='sphere'?new THREE.SphereGeometry(1,12,8):new THREE.CylinderGeometry(1,1,1,12);
  if(p.shape==='sphere')geometry.scale(p.size[0]/2,p.size[1]/2,p.size[2]/2);
  if(p.shape==='cylinder')geometry.scale(p.size[0]/2,p.size[1],p.size[2]/2);
  const mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:p.color}));
  mesh.name=p.id;mesh.position.fromArray(p.position);mesh.rotation.set(...p.rotation);parts.set(p.id,mesh);
 }
 for(const p of spec.parts)(p.parent_id?parts.get(p.parent_id):scene).add(parts.get(p.id));
 const clips=spec.clips.map(c=>{const values=[],times=[],steps=Math.max(1,Math.ceil(Math.abs(c.to_degrees-c.from_degrees)/90));for(let i=0;i<=steps;i++){const angle=c.from_degrees+(c.to_degrees-c.from_degrees)*i/steps;times.push(c.duration_seconds*i/steps);const axis=new THREE.Vector3();axis[c.axis]=1;values.push(...parts.get(c.part_id).quaternion.clone().multiply(new THREE.Quaternion().setFromAxisAngle(axis,angle*Math.PI/180)).toArray())}return new THREE.AnimationClip(c.name,c.duration_seconds,[new THREE.QuaternionKeyframeTrack(c.part_id+'.quaternion',times,values)])});
 return {scene,clips};
}
'''
