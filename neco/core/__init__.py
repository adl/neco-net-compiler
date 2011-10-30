""" Main compiler interface. """

import itertools
from collections import defaultdict
from snakes.nets import *
import neco.config as config
from neco.utils import flatten_lists
import netir, nettypes
from info import *
from itertools import izip_longest

from glue import FactoryManager

################################################################################

class CompilingEnvironment(object):
    """ Contains data that need to be shared between components;
    """

    def __init__(self):
        """ Build a new compiling environment.
        """
        self._succ_function_names = {}
        self._process_succ_function_names = set()

    @property
    def succ_functions(self):
        """ transition specific successor function names. """
        return self._succ_function_names.values()

    def get_succ_function_name(self, transition_info):
        """ Returns the name of a successor function.

        @param transition_info:
        @type transition_info: C{}
        """
        return self._succ_function_names[transition_info.name]

    def register_succ_function(self, transition_info, function_name):
        """ Registers a function name as a successor function.

        @param transition_info: transition that produced the function.
        @type transition_info: C{TransitionInfo}
        @param function_name: successor function name
        @type function_name: C{str}
        """
        self._succ_function_names[transition_info.name] = function_name


    @property
    def process_succ_functions(self):
        """ process successor function names. """
        return self._process_succ_function_names

    def register_process_succ_function(self, function_name):
        """ Registers a function name as a process successor function.

        @param function_name: successor function name
        @type function_name: C{str}
        """
        self._process_succ_function_names.add(function_name)

################################################################################

class ProcessSuccGenerator(object):
    """ Class that produces a succ function specific to ABCD processes
    """

    def __init__(self, env, net_info, process_info,
                 function_name, marking_type):
        """
        Function that handles the production of processor specific successor
        functions.

        @param env: compiling environment.
        @param net_info: Petri net info structure.
        @param process_info: ProcessInfo of the process.
        @param function_name: name of produced function.
        @param marking_type: used marking type
        """
        self._env = env
        self._net_info = net_info
        self._process_info = process_info
        self._function_name = function_name
        self._marking_type = marking_type
        variable_provider = VariableProvider(set([function_name]))
        self._variable_provider = variable_provider
        self._flow_variable = variable_provider.new_variable(type = TypeInfo.Int)

        self._builder = netir.Builder()

        self._arg_marking = variable_provider.new_variable(type = marking_type.type)
        self._arg_marking_set = variable_provider.new_variable(type = marking_type.container_type)

        env.register_process_succ_function(function_name)

    def __call__(self):
        """ Generate function.
        """
        env = self._env
        function_name   = self._function_name
        arg_marking     = self._arg_marking
        arg_marking_set = self._arg_marking_set
        process_info    = self._process_info

        if not config.get('process_flow_elimination'):
            return

        builder = self._builder

        builder.begin_function_SuccP( function_name      = function_name,
                                      arg_marking        = arg_marking,
                                      arg_marking_set    = arg_marking_set,
                                      process_info       = process_info,
                                      flow_variable      = self._flow_variable,
                                      variable_provider  = self._variable_provider )

        # enumerate places with not empty post
        ne_post = [ place for place in process_info.flow_places if place.post ]

        current_flow = self._flow_variable

        read=builder.ReadFlow(marking=arg_marking,
                              process_name=process_info.name)
        builder.emit_Assign(variable=current_flow, expr=read)

        for i, flow_place in enumerate(ne_post):
            if i == 0:
                builder.begin_If( condition = builder.FlowCheck(marking=arg_marking,
                                                                current_flow=current_flow,
                                                                place_info=flow_place) )
            else:
                builder.begin_Elif( condition = builder.FlowCheck(marking=arg_marking,
                                                                  current_flow=current_flow,
                                                                  place_info=flow_place) )

            #produced inside each if block:
            for transition in flow_place.post:
                name = env.get_succ_function_name( transition )
                builder.emit_ProcedureCall( function_name = name,
                                            arguments = [ netir.Name( name = arg_marking_set.name ),
                                                          netir.Name( name = arg_marking.name ) ] )

        builder.end_all_blocks()
        builder.end_function()
        return builder.ast()

################################################################################

class SuccTGenerator(object):
    """ Transition specific succ function generator.

    This class is used for building a transition specific succ function.
    """

    def __init__(self, env, net_info, builder, transition, function_name, marking_type):
        """ Builds the transition specific succ function generator.

        @param builder: builder structure for easier ast construction
        @type builder: C{neco.core.netir.Builder}
        @param transition: considered transition
        @type transition: C{neco.core.info.TransitionInfo}
        @param function_name: function name
        @type function_name: C{str}
        @param ignore_flow: ignore flow control places ?
        @type ignore_flow: C{bool}
        """
        self.net_info = net_info
        self.builder = builder
        self.transition = transition
        self.function_name = function_name
        self.marking_type = marking_type
        self._ignore_flow = self._ignore_flow = config.get('process_flow_elimination')
        self.env = env

        # this helper will create new variables and take care of shared instances
        helper = SharedVariableHelper( transition.shared_input_variables(),
                                       WordSet(transition.variables().keys()) )
        self.variable_helper = helper

        if config.get('optimise'):
            self.transition.order_inputs()

        # function arguments
        self.arg_marking = helper.new_variable(type = marking_type.type)
        self.marking_set = helper.new_variable(type = marking_type.container_type)

        # create function
        self.builder.begin_function_SuccT( function_name     = self.function_name,
                                           arg_marking       = self.arg_marking,
                                           arg_marking_set   = self.marking_set,
                                           transition_info   = self.transition,
                                           variable_provider = helper )

        # self.builder.emit(netir.Print("calling {}".format(self.function_name)))

        # remember this succ function
        env.register_succ_function(transition, function_name)

    def try_unify_shared_variable(self, variable):
        """ Produces the unification process for shared variables if all occurences have been used.

        In the context where different names represent a unique variable,
        this function compares these names and use a witness for the final
        variable name.

        @param variable: initial variable (appearing in the model)
        @type variable: C{str}
        """
        assert( isinstance(variable, VariableInfo) )
        if self.variable_helper.is_shared(variable):
            if self.variable_helper.all_used(variable):
                if not self.variable_helper.unified(variable):
                    local_variables = self.variable_helper.get_local_variables(variable)
                    first_local = local_variables.pop(-1)

                    # compare values
                    self.builder.begin_If( netir.Compare(left=netir.Name(first_local.name),
                                                         ops = [netir.EQ() for i in local_variables],
                                                         comparators = [netir.Name(loc_var.name) for loc_var in local_variables] ) )

                    # build a witness with the initial variable name
                    self.builder.emit_Assign( variable = variable,
                                              expr = netir.Name(first_local.name) )
                    self.variable_helper.set_unified(variable)
                    return True
            return False

    def gen_enumerators(self):
        """ Produces all the token enumeration blocs.
        """

        builder = self.builder
        trans = self.transition
        variable_helper = self.variable_helper
        trans.variable_helper = variable_helper
        #consume = self.consume

        # places that provides multiple tokens, _cannot_ be used with by index acces
        multi_places = trans.input_multi_places
        for place in multi_places:
            place_type = self.marking_type.get_place_type_by_name(place.name)
            place_type.disable_by_index_deletion()

        if config.get('optimise'):
            trans.order_inputs()

        # loop over inputs
        for input in trans.inputs:
            if self._ignore_flow and input.place_info.flow_control:
                continue

            builder.emit_Comment("Enumerate {input} - place: {place}".format(input=input, place=input.place_name))

            # use index access if available
            place_type = self.marking_type.get_place_type_by_name(input.place_info.name)
            if place_type.provides_by_index_access and input.place_info not in multi_places:
                index = variable_helper.new_variable(type = TypeInfo.Int)
            else:
                index = None

            # produce the enumeration and tests according to the input's type

            # variable
            if input.is_Variable:
                variable = input.variable
                # if the variable is shared a new variable is produced, the variable is used otherwise
                local_variable = variable_helper.new_variable_occurence(variable)

                # notify that the variable is used
                variable_helper.mark_as_used(variable, local_variable)

                builder.begin_TokenEnumeration( arc = input,
                                                token_var = local_variable,
                                                marking = self.arg_marking,
                                                place_name = input.place_name )

                input.data.register('local_variable', local_variable)
                input.data.register('index', index)

                self.try_unify_shared_variable(variable)

            # test
            elif input.is_Test:
                inner = input.inner

                if inner.is_Variable:
                    variable = inner

                    local_variable = variable_helper.new_variable_occurence(variable)
                    variable_helper.mark_as_used( variable, local_variable )

                    builder.begin_TokenEnumeration( arc = input,
                                                    token_var = local_variable,
                                                    marking = self.arg_marking,
                                                    place_name = input.place_name )

                    self.try_unify_shared_variable(variable)

                    input.data.register('local_variable', local_variable)
                    input.data.register('index', index)

                elif inner.is_Value:
                    place_info = self.net_info.place_by_name( input.place_name )
                    place_type = self.marking_type.get_place_type_by_name( place_info.name )

                    local_variable = variable_helper.new_variable(type=place_type.token_type)

                    # get a token
                    builder.begin_TokenEnumeration( arc = input,
                                                    token_var = local_variable,
                                                    marking = self.arg_marking,
                                                    place_name = input.place_name )

                    if not place_info.type.is_BlackToken:
                        # check token value
                        builder.begin_If( netir.Compare( left  = netir.Name( name = local_variable.name ),
                                                         ops = [ netir.EQ() ],
                                                         comparators = [ netir.Value( value = input.value,
                                                                                      place_name = input.place_name ) ] ) )
                    input.data.register('local_variable', local_variable)
                    #input.data.register('input', input)

                elif inner.is_Tuple:
                    # produce names for tuple components
                    self._gen_names( inner )

                    place_info = self.net_info.place_by_name( input.place_name )
                    place_type = self.marking_type.get_place_type_by_name( place_info.name )
                    token_var = inner.data['local_variable']

                    # get a tuple
                    builder.begin_TokenEnumeration( arc = input,
                                                    token_var = token_var,
                                                    marking = self.arg_marking,
                                                    place_name = input.place_name )

                    if not (inner.type.is_TupleType and len(inner.type) == len(inner)):
                        # check its type
                        builder.begin_CheckTuple( tuple_var = token_var,
                                                  tuple_info = inner )

                    self._gen_tuple_decomposition(input, inner)

                else:
                    raise NotImplementedError, "ArcTest : inner = %s" % inner

            # value
            elif input.is_Value:
                place_info = self.net_info.place_by_name(input.place_name)
                place_type = self.marking_type.get_place_type_by_name(place_info.name)

                local_variable = variable_helper.new_variable(place_type.token_type)

                # get a token
                builder.begin_TokenEnumeration( arc = input,
                                                token_var = local_variable,
                                                marking = self.arg_marking,
                                                place_name = input.place_name )

                input.data.register('local_variable', local_variable)
                input.data.register('index', index)

                if not place_info.type.is_BlackToken:
                    # check token value
                    builder.begin_If( netir.Compare( left  = netir.Name( name = local_variable.name ),
                                                     ops = [ netir.EQ() ],
                                                     comparators = [ netir.Value( value = input.value,
                                                                                  place_name = input.place_name ) ] ) )


            # flush
            elif input.is_Flush:
                inner = input.inner
                if inner.is_Variable:
                    if variable_helper.is_shared(inner):
                        raise NotImplementedError

                    input.data.register('local_variable', inner)
                else:
                    raise NotImplementedError, "flush %s" % repr(inner)

            # tuple
            elif input.is_Tuple:
                # produce names for tuple components
                self._gen_names( input.tuple )

                place_info = self.net_info.place_by_name( input.place_name )
                place_type = self.marking_type.get_place_type_by_name( place_info.name )
                token_variable = input.tuple.data['local_variable']

                # get a tuple
                builder.begin_TokenEnumeration( arc = input,
                                                token_var = token_variable,
                                                marking = self.arg_marking,
                                                place_name = input.place_name ) # no index access

                if not (input.tuple.type.is_TupleType and len(input.tuple.type) == len(input.tuple)):
                    # check its type
                    builder.begin_CheckTuple( tuple_var = token_variable,
                                              tuple_info = input.tuple )


                self._gen_tuple_decomposition(input, input.tuple)
                input.data.register('index', index)

            elif input.is_MultiArc:
                variables = set()
                values = {} # variable -> value
                # sub_arcs as variables
                for sub_arc in input.sub_arcs:
                    if sub_arc.is_Variable:
                        variable = sub_arc.variable
                        local_variable = variable_helper.new_variable_occurence(variable)
                        variable_helper.mark_as_used( variable, local_variable )

                        variables.add(variable)
                        sub_arc.data.register('local_variable', local_variable)
                        sub_arc.data.register('index', variable_helper.new_variable(TypeInfo.Int))

                    elif sub_arc.is_Value:
                        variable = variable_helper.new_variable( type = place_type.token_type )

                        variables.add(variable)
                        sub_arc.data.register('local_variable', variable)
                        sub_arc.data.register('index', variable_helper.new_variable(TypeInfo.Int))

                        if sub_arc.value.raw != dot and not place_type.token_type.is_BlackToken:
                            values[variable] = sub_arc.value

                    else:
                        raise NotImplementedError, sub_arc


                builder.begin_MultiTokenEnumeration( multiarc = input,
                                                     marking = self.arg_marking,
                                                     place_name = input.place_name )

                for variable, value in values.iteritems():
                    builder.begin_If( netir.Compare( left  = netir.Name( name = variable.name ),
                                                     ops = [ netir.EQ() ],
                                                     comparators = [ netir.Value( value = value,
                                                                                  place_name = input.place_name ) ] ) )

                for variable in variables:
                    self.try_unify_shared_variable(variable)

            else:
                raise NotImplementedError, input.arc_annotation.__class__

    def _gen_names(self, token_info):
        """
        """
        if token_info.is_Tuple:
            token_info.data.register('local_variable',
                                     self.variable_helper.new_variable())
            for component in token_info:
                self._gen_names(component)

        elif token_info.is_Variable:
            token_info.data.register('local_variable',
                                     self.variable_helper.new_variable_occurence(token_info))

        elif token_info.is_Value:
            token_info.data.register('local_variable',
                                     self.variable_helper.new_variable())

        else:
            raise NotImplementedError, token_info

    def _gen_tuple_decomposition(self, input, tuple):
        """ Produce the decomposition of a tuple (pattern matching).

        @param input: input arc
        @type input: C{neco.core.info.ArcInfo}
        @param tuple: tuple to decompose
        @type tuple: C{tuple}
        """
        builder = self.builder
        trans = self.transition
        variable_helper = self.variable_helper

        builder.begin_Match( tuple_info = tuple )

        def base_names(token_info):
            if token_info.is_Tuple:
                local_variable = token_info.data['local_variable']
                names = set( [ (local_variable, local_variable) ] )
                for component in token_info:
                    names.union( base_names(component) )
                return names

            elif token_info.is_Variable:
                return set([ (token_info, token_info.data['local_variable']) ])

            elif token_info.is_Value:
                local_variable = token_info.data['local_variable']
                return set([ (local_variable, local_variable) ])

            else:
                raise NotImplementedError, token_info

        for (inner, type) in izip_longest(tuple.split(), tuple.type.split(), fillvalue=TypeInfo.AnyType):
            if not type.is_AnyType:
                inner.update_type(type)

            if inner.is_Tuple:
                tuple_var = inner.data['local_variable']

                if not (inner.type.is_TupleType and len(inner.type) == len(inner)):
                    # check its type
                    builder.begin_CheckTuple( tuple_var = tuple_var,
                                              tuple_info = inner )

                self._gen_tuple_decomposition(input, inner)

            elif inner.is_Variable:
                variable_helper.mark_as_used( inner, inner.data['local_variable'] )
                self.try_unify_shared_variable( inner )

    def gen_computed_production(self, output, computed_productions):
        """ Compute an expression on an output arc and store the
        result in a dict.

        This allows an expression to be computed as soon as
        possible. It means that when all tokens involved in the
        expression are available, we will compute the expression and
        store the result.

        @param output output to be used
        @param computed_productions output -> productions list
        @type computed_productions defaultdict(list)

        """
        trans = self.transition
        helper = self.variable_helper
        builder = self.builder


        output_impl_type = self.marking_type.get_place_type_by_name( output.place_info.name ).token_type

        if self._ignore_flow and output.place_info.flow_control:
            return

        elif output.is_Expression:
            # new temporary variable
            variable = self.variable_helper.new_variable(output_impl_type)

            # evaluate and assign the result to the variable
            builder.emit_Assign( variable = variable,
                                 expr = netir.PyExpr(output.expr) )
            check = True
            try:
                print ">>>> BEGIN TO DO %s <<<< %s" % (__FILE__, output.expr.raw)
                value = eval(output.expr.raw)
                if output.place_info.type.contains(value):
                    check = False
                print ">>>> END TO DO %s <<<< " % __FILE__
            except:
                pass

            computed_productions[output].append(netir.Name( variable.name ))

        elif output.is_Value:
            value = output.value
            variable = self.variable_helper.new_variable(output_impl_type)
            check = True

            v = eval(repr(value.raw))
            if v == dot and output.place_info.type.is_BlackToken:
                check = False

            # check its type
            if check:
                builder.emit_Assign( variable = variable,
                                     expr = netir.PyExpr( ExpressionInfo( repr(output.value.raw) ) ) )

                r = netir.Name( variable.name )
            else:
                r = netir.PyExpr( ExpressionInfo( repr(output.value.raw) ) )

            computed_productions[output].append(r)

        elif output.is_Variable:
            variable = output.variable
            if not (output_impl_type.is_AnyType or (variable.type == output_impl_type)):
                builder.begin_CheckType( variable = variable,
                                         type = output_impl_type )

            computed_productions[output].append(netir.Name( variable.name ))

        elif output.is_Flush:
            # no type check, object places
            pass

        elif output.is_Tuple:
            # no type check, WARNING: may be unsound !
            pass

        elif output.is_MultiArc:
            # temporary list for holding productions
            tmp = defaultdict(list)

            for subarc in output.sub_arcs:
                # produce code for the computation and variable assignation
                self.gen_computed_production(subarc, tmp)

            # retrieve productions
            for value in tmp.itervalues():
                computed_productions[output].append(value)

        else:
            raise NotImplementedError, output.arc_annotation.__class__


    def __call__(self):
        """ Build an instance of SuccT from a TransitionInfo object.

        @return: successor function abstract representation.
        """
        trans = self.transition
        helper = self.variable_helper
        builder = self.builder

        if config.get('trace_calls'):
            builder.emit_Print("calling " + self.function_name)

        # for loops
        self.gen_enumerators()

        # guard
        guard = ExpressionInfo( trans.trans.guard._str )
        try:
            if eval(guard.raw) != True:
                builder.begin_GuardCheck( condition = netir.PyExpr(guard) )
        except:
            builder.begin_GuardCheck( condition = netir.PyExpr(guard) )

        if config.get('trace_calls'):
            builder.emit_Print("  guard valid in " + self.function_name)

        computed_productions = defaultdict(list)
        for output in trans.outputs:
            self.gen_computed_production(output, computed_productions)

        new_marking = helper.new_variable(self.marking_type.type)
        builder.emit_MarkingCopy( dst = new_marking,
                                  src = self.arg_marking,
                                  mod = trans.modified_places() )

        # consume
        for arc in trans.inputs:
            builder.emit_Comment(message = "Consume {arc} - place: {place}".format(arc=arc, place=arc.place_name))

            if self._ignore_flow and arc.place_info.flow_control:
                continue
            elif arc.is_Variable:
                builder.emit_RemToken( marking = new_marking,
                                       place_name = arc.place_name,
                                       token_expr = netir.Name(arc.data['local_variable'].name),
                                       use_index = arc.data['index'] )
            elif arc.is_Test:
                pass # do not consume !

            elif arc.is_Flush:
                inner = arc.inner
                if inner.is_Variable:
                    builder.emit_FlushIn( token_var = arc.data['local_variable'],
                                          marking = new_marking,
                                          place_name = arc.place_name )
                else:
                    raise NotImplementedError, "inner : %s" % repr(inner)

            elif arc.is_Value:
                builder.emit_RemToken( marking = new_marking,
                                       place_name = arc.place_name,
                                       token_expr = netir.Value( value = arc.value,
                                                                 place_name = arc.place_name ),
                                       use_index = arc.data['index'] )

            elif arc.is_Tuple:
                builder.emit_RemTuple( marking = new_marking,
                                       place_name = arc.place_name,
                                       tuple_expr = netir.Name(arc.tuple.data['local_variable'].name) )

            elif arc.is_MultiArc:
                names = {}
                for sub_arc in arc.sub_arcs:
                    if sub_arc.is_Variable:
                        builder.emit_RemToken( marking = new_marking,
                                               place_name = arc.place_name,
                                               token_expr = netir.Name(sub_arc.data['local_variable'].name),
                                               use_index = sub_arc.data['index'] )
                    elif sub_arc.is_Value:
                        builder.emit_RemToken( marking = new_marking,
                                               place_name = arc.place_name,
                                               token_expr = netir.Value( value = sub_arc.value,
                                                                         place_name = arc.place_name ),
                                               use_index = sub_arc.data['index'] )

                    else:
                        raise NotImplementedError, sub_arc.arc_annotation
            else:
                raise NotImplementedError, arc.arc_annotation

        # produce
        for output_arc in trans.outputs:
            builder.emit_Comment(message="Produce {output_arc} - place: {place}".format(output_arc=output_arc, place=output_arc.place_name))
            if self._ignore_flow and output_arc.place_info.flow_control:
                new_flow = [ place for place in trans.post if place.flow_control ]
                assert(len(new_flow) == 1)
                builder.emit_UpdateFlow( marking = new_marking,
                                         place_info = new_flow[0] )


            elif output_arc.is_Expression:
                if computed_productions.has_key(output_arc):
                    token_expr = computed_productions[output_arc]

                builder.emit_AddToken( marking = new_marking,
                                       place_name = output_arc.place_name,
                                       token_expr = token_expr )

            elif output_arc.is_Value:
                if computed_productions.has_key(output_arc):
                    token_expr = computed_productions[output_arc]
                else:
                    #
                    # TO DO TRY REPR
                    #
                    value = output_arc.value
                    token_expr = netir.PyExpr( ExpressionInfo( repr(value.raw) ))

                builder.emit_AddToken( marking = new_marking,
                                       place_name = output_arc.place_name,
                                       token_expr = token_expr )

            elif output_arc.is_Variable:
                if computed_productions.has_key(output_arc):
                    token_expr = computed_productions[output_arc]
                else:
                    value = output_arc.value
                    token_expr = netir.Name( output_arc.name )

                builder.emit_AddToken( marking = new_marking,
                                       place_name = output_arc.place_name,
                                       token_expr = token_expr )

            elif output_arc.is_Flush:
                inner = output_arc.inner
                if inner.is_Variable:
                    produced_token = inner.name
                    builder.emit_FlushOut( marking = new_marking,
                                           place_name = output_arc.place_name,
                                           token_expr = netir.Name(produced_token) )

                elif inner.is_Expression:
                    builder.emit_FlushOut( marking = new_marking,
                                           place_name = output_arc.place_name,
                                           token_expr = netir.PyExpr(inner) )
                else:
                    raise NotImplementedError, "Flush.inner : %s" % inner

            elif output_arc.is_Tuple:
                # to do: if arc then use var
                if (False):
                    pass
                # general case:
                else:
                    builder.emit_TupleOut( marking = new_marking,
                                           place_name = output_arc.place_name,
                                           tuple_info = output_arc.tuple )
            elif output_arc.is_MultiArc:
                for production in computed_productions[output_arc]:
                    builder.emit_AddToken( marking = new_marking,
                                           place_name = output_arc.place_name,
                                           token_expr = production )

            else:
                raise NotImplementedError, output_arc.arc_annotation.__class__

        # add marking to set
        builder.emit_AddMarking( marking_set = self.marking_set,
                                 marking = new_marking)
        # end function
        builder.end_all_blocks()
        builder.end_function()
        if config.get('debug'):
            print self.transition.variable_informations()

        return builder.ast()

################################################################################

class Compiler(object):
    """ The main compiler class.

    This class is used to produce a library from a snake.nets.PetriNet.
    """

    def __init__(self, net, factory_manager = FactoryManager(), atoms = []):
        """ Initialise the compiler from a Petri net.

        builds the basic info structure from the snakes petri net representation

        @param net: Petri net.
        @type net: C{snakes.nets.PetriNet}
        """
        self.env = CompilingEnvironment()
        self.net = net
        self.dump_enabled = False
        self.debug = False

        self._ignore_flow = config.get('process_flow_elimination')
        FactoryManager.update( factory_manager )
        fm = FactoryManager.instance()

        self.net_info = NetInfo(net)
        self.markingtype_class = "StaticMarkingType"
        self.marking_type = fm.markingtype_factory.new(self.markingtype_class)

        self.optimisations = []
        self.rebuild_marking_type()

        self.successor_function_nodes = []
        self.process_successor_function_nodes = []
        self.main_successor_function_node = None
        self.init_function_node = None

        self.global_names = WordSet([])
        self._successor_functions = []
        # TODO hardcoded for testing
        self.atoms = [] # [ info.AtomInfo(atom, ['s1', 's2']) for atom in atoms ]

    @property
    def successor_functions(self):
        return self._successor_functions

    @property
    def marking_type(self):
        """ Marking type. """
        return self._marking_type

    @marking_type.setter
    def marking_type(self, marking_type):
        self._marking_type = marking_type
        self.markingset_type = FactoryManager.instance().markingsettype_factory.new_MarkingSetType(marking_type)

    @property
    def factory_manager(self):
        """ Factory Manager. """
        return FactoryManager.instance()

    @factory_manager.setter
    def factory_manager(self, factory_manager):
        FactoryManager.update( factory_manager )

    def set_marking_type_by_name(self, markingtype_class):
        """ Specify set marking type by name (places will be rebuild).

        @param markingtype_class: marking type name.
        @type markingtype_class: C{str}
        """
        self.markingtype_class = markingtype_class
        self.marking_type = FactoryManager.instance().markingtype_factory.new(self.markingtype_class)
        self.rebuild_marking_type()

    def available_marking_types(self):
        """ Get all available marking types.

        @return: marking types
        @rtype: C{list<str>}
        """
        return self.markingtype_factory.products()

    def rebuild_marking_type(self):
        """ Rebuild the marking type. (places will be rebuild) """
        # add place types to the marking type
        for place_info in self.net_info.places:
            self.marking_type.append( place_info )

        fm = FactoryManager.instance()
        self.marking_type.gen_types()
        if self.dump_enabled:
            print self.marking_type

    def add_optimisation(self, opt):
        """ Add an optimisation.

        @param opt: optimisation pass.
        @type opt: C{neco.opt.OptimisationPass}
        """
        global g_opt
        g_opt = True
        self.optimisations.append(opt)
        # update factories and updates modules
        #self.factory_manager = opt.update_factory_manager(self.factory_manager)

        opt.update_factory_manager()
        fm = FactoryManager.instance()
        self.marking_type = fm.markingtype_factory.new(self.markingtype_class)
        self.rebuild_marking_type()

    def optimise_netir(self):
        """ Run optimisation passes on the AST. """
        for opt in self.optimisations:
            self.successor_function_nodes = [ opt.transform_ast(net_info = self.net_info, node = node)
                                              for node in self.successor_function_nodes ]
            self.process_successor_function_nodes = [ opt.transform_ast(net_info = self.net_info, node = node)
                                                      for node in self.process_successor_function_nodes ]
            self.successor_function_nodes = flatten_lists( self.successor_function_nodes )
            self.process_successor_function_nodes = flatten_lists( self.process_successor_function_nodes )


    def _gen_all_spec_succs(self):
        """ Build all needed instances of transition specific
        successor functions abstract representations.

        This method updates C{self._nodes}.
        """
        list = []
        for i,t in enumerate(self.net_info.transitions):
            function_name = "succs_%d" % i # TO DO use name + escape
            if self.dump_enabled:
                print function_name + " <=> " + t.name
            assert( function_name not in self.global_names )
            self.global_names.add(function_name)

            gen = SuccTGenerator(self.env,
                                 self.net_info,
                                 netir.Builder(),
                                 t,
                                 function_name,
                                 self.marking_type)
            list.append( gen() )

            self._successor_functions.append( (function_name, t.process_name) )
        return list

    def _gen_all_process_spec_succs(self):
        """ Build all needed instances of process specific successor
        function abstract representation nodes.
        """
        list = []
        if config.get('process_flow_elimination'):
            for i, process in enumerate(self.net_info.process_info):
                function_name = "succP_%d" % i # process.name
                gen = ProcessSuccGenerator(self.env,
                                           self.net_info,
                                           process,
                                           function_name,
                                           self.marking_type)
                list.append( gen() )
        return list

    def _gen_main_succ(self):
        """ Produce main successor function abstract representation node. """

        self.succs_function = 'succs'
        variable_provider = VariableProvider(set([self.succs_function]))
        arg_marking = variable_provider.new_variable( type = self.marking_type.type )
        arg_marking_set = variable_provider.new_variable( type = self.marking_type.container_type )

        builder = netir.Builder()
        builder.begin_function_Succs( function_name = "succs",
                                      arg_marking = arg_marking,
                                      arg_marking_set = arg_marking_set,
                                      variable_provider = variable_provider)

        markingset_node  = netir.Name(arg_marking_set.name)
        marking_arg_node = netir.Name(arg_marking.name)

        if self._ignore_flow:
            for function_name in self.env.process_succ_functions:
                builder.emit_ProcedureCall( function_name = function_name,
                                            arguments = [ markingset_node,
                                                          marking_arg_node ] )

        else:
            for function_name in self.env.succ_functions:
                builder.emit_ProcedureCall( function_name = function_name,
                                            arguments = [ markingset_node,
                                                          marking_arg_node ] )

        builder.end_function()
        return builder.ast()

    def _gen_init(self):
        """ Produce initial marking function abstract representation node. """

        marking = VariableInfo('marking', type = self.marking_type.type)
        variable_provider = VariableProvider(set(['init', marking.name]))

        builder = netir.Builder()
        builder.begin_function_Init(function_name = 'init',
                                    marking = marking,
                                    variable_provider = variable_provider)

        for place_info in self.net_info.places:
            if len(place_info.tokens) > 0:
                # add tokens
                if self._ignore_flow and place_info.flow_control:
                    builder.emit_UpdateFlow(marking = marking,
                                            place_info = place_info);
                    continue

                for token in place_info.tokens:
                    info = TokenInfo.from_raw( token )
                    if info.is_Tuple:
                        builder.emit_TupleOut( marking = marking,
                                               place_name = place_info.name,
                                               tuple_info = info )
                    elif info.is_Value:
                        t = info.type
                        if t in [ TypeInfo.Int, TypeInfo.BlackToken ]:
                            builder.emit_AddToken( marking = marking,
                                                   place_name = place_info.name,
                                                   token_expr = netir.Token( value = token,
                                                                             place_name = place_info.name ) )
                        elif t.is_UserType or t.is_AnyType:
                            expr = netir.Pickle( obj = info.raw )
                            builder.emit_AddToken( marking = marking,
                                                   place_name = place_info.name,
                                                   token_expr = expr )
                        else:
                            raise NotImplementedError, info.value.type()
                    else:
                        raise NotImplementedError

        builder.end_function()
        return builder.ast()

    def gen_netir(self):
        """ produce abstract representation nodes.
        """
        self.successor_function_nodes = flatten_lists( self._gen_all_spec_succs() )
        self.process_successor_function_nodes = flatten_lists( self._gen_all_process_spec_succs() )
        self.main_successor_function_node = flatten_lists( self._gen_main_succ() )
        self.init_function_node = flatten_lists( self._gen_init() )

################################################################################

if __name__ == "__main__":
    import doctest
    doctest.testmod()

################################################################################
# EOF
################################################################################
