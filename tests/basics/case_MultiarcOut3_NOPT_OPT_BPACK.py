from snakes.nets import *

net = PetriNet('Net')
net.processes = []


s1 = Place('s1', [42], tInteger)
s1.one_safe = True
s1.process_name = 'a'
s1.flow_control = False

s2 = Place('s2', [], tInteger)
s2.one_safe = False
s2.process_name = 'a'
s2.flow_control = False

net.add_place(s1)
net.add_place(s2)

transition = Transition('t', Expression('True'))
net.add_transition(transition)

net.add_input('s1', 't', Variable('x'))
net.add_output('s2', 't', MultiArc([Expression("x/2"), Variable("x")]))
