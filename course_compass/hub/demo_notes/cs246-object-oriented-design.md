# Object-Oriented Design Basics

## Core ideas

Object-oriented design groups data with the operations that act on it. A class describes the data and behaviour; an object is one instance of a class created at run time.

- **Encapsulation**: hiding an object's internal state behind a public interface so other code can only change it through methods that keep it valid.
- **Invariant**: a property of an object's state that every public method must keep true, such as a stack's size never being negative.
- **Inheritance**: defining a new class from an existing one so the subclass reuses and extends the base class's interface.
- **Polymorphism**: calling the same method on objects of different classes and getting the behaviour of each object's actual class, chosen at run time through virtual dispatch.

## Resource management

- **RAII**: resource acquisition is initialization, the C++ idiom where a constructor acquires a resource and the destructor releases it, so a resource is freed even when an exception is thrown.
- **Copy constructor**: the constructor that builds a new object as a copy of an existing one; with owned heap memory it must perform a deep copy.
- **Move constructor**: the constructor that steals the resources of a temporary object instead of copying them, leaving the temporary in a valid empty state.

The rule of five says that a class which defines any of the destructor, copy constructor, copy assignment, move constructor or move assignment usually needs all five.

## Design patterns

- **Observer pattern**: a subject keeps a list of observers and notifies each one when its state changes, so the subject does not depend on concrete observer classes.
- **Decorator pattern**: wrapping an object in another object with the same interface to add behaviour at run time without changing the original class.
- **Factory method**: a method that creates objects through an interface so the caller does not name the concrete class it gets back.

Coupling measures how much modules depend on each other; cohesion measures how closely the parts of one module belong together. Good design aims for low coupling and high cohesion.
